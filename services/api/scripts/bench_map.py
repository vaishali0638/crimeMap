"""Map query load test (ADR-017): the real API code, in-process, against the local Compose database
as the crimemap_app role (row-level security on), 10 concurrent requests x 6 rounds per query.

    set -a; source ../../.env; set +a
    .venv/bin/python scripts/bench_map.py [--concurrency 10] [--rounds 6] [--only "HEX"]

Tokens are minted with the test signing key and verified by the in-process app only; the running
API's token trust is not involved. Results depend on the host (see docs/phase-3-report.md). This is
not k6: the load generator shares the event loop with the API, which adds some queueing.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time

import httpx
import psycopg

sys.path.insert(0, ".")  # run from services/api

from tests.support import bearer, local_verifier, mint

from crimemap_api.main import create_app
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

QUERIES = {
    "HEX 500 m, PDY, 24 months": "region=PDY&aggregation=HEX&hex_size_m=500&period_from=2024-10-01&period_to=2026-09-30",
    "HEX 250 m, PDY, 90 days": "region=PDY&aggregation=HEX&hex_size_m=250&period_from=2026-07-03&period_to=2026-09-30",
    "BEAT, PDY, 12 months, theft": "region=PDY&aggregation=BEAT&category=PROPERTY_THEFT&period_from=2025-10-01&period_to=2026-09-30",
    "POINTS, PDY-S01, 90 days": "region=PDY&station=PDY-S01&aggregation=POINTS&period_from=2026-07-03&period_to=2026-09-30",
    "POINTS, PDY, 30 days, view box": "region=PDY&aggregation=POINTS&period_from=2026-09-01&period_to=2026-09-30&bbox=79.78,11.90,79.84,11.96",
    # PY Dashboard boards (Phase 4): full paths; each also reads the previous period for comparison.
    "TREND top-spot, ALL, 12 months": "/api/v1/dashboard/trends/top-spot?region=ALL&period_from=2025-10-01&period_to=2026-09-30",
    "TREND station-rate, ALL, 12 months": "/api/v1/dashboard/trends/station-rate?region=ALL&period_from=2025-10-01&period_to=2026-09-30",
    "TREND week, PDY, 12 months": "/api/v1/dashboard/trends/week?region=PDY&period_from=2025-10-01&period_to=2026-09-30",
    "DETAILS PDY-S01, 90 days": "/api/v1/dashboard/stations/PDY-S01?period_from=2026-07-03&period_to=2026-09-30",
    # Analytics Workbench (Phase 5): 12 months of Puducherry, comparison with the previous period.
    "AN overview, PDY, 12 months": "/api/v1/analytics/overview?region=PDY&period_from=2025-10-01&period_to=2026-09-30",
    "AN location HEX 500, PDY, 12 months": "/api/v1/analytics/location?region=PDY&period_from=2025-10-01&period_to=2026-09-30&unit=HEX&hex_size_m=500",
    "AN case trends, PDY, 12 months": "/api/v1/analytics/case-trends?region=PDY&period_from=2025-10-01&period_to=2026-09-30",
    "AN data quality, PDY, 12 months": "/api/v1/analytics/data-quality?region=PDY&period_from=2025-10-01&period_to=2026-09-30",
}


async def timed(
    api: httpx.AsyncClient, url: str, headers: dict[str, str], times: list[float]
) -> None:
    start = time.perf_counter()
    response = await api.get(url, headers=headers)
    times.append((time.perf_counter() - start) * 1000)
    if response.status_code != 200:
        raise RuntimeError(f"{response.status_code}: {response.text[:300]}")


async def main(concurrency: int, rounds: int, only: str | None = None) -> None:
    db = os.environ["CRIMEMAP_DB_NAME"]
    migrator = f"postgresql://crimemap_migrator:{os.environ['CRIMEMAP_MIGRATOR_PASSWORD']}@127.0.0.1:55432/{db}"
    with psycopg.connect(migrator) as conn:
        row = conn.execute(
            "SELECT idp_subject FROM core.app_user WHERE username = 'demo.analyst'"
        ).fetchone()
    if row is None:
        raise SystemExit("demo.analyst has not signed in yet (no app_user row)")
    settings = Settings(
        database_url=f"postgresql+asyncpg://crimemap_app:{os.environ['CRIMEMAP_APP_PASSWORD']}@127.0.0.1:55432/{db}",  # type: ignore[arg-type]
        redis_url="redis://unused:6379/0",  # type: ignore[arg-type]
        oidc_issuer="https://keycloak.test/realms/crimemap-test",
        oidc_jwks_url="https://keycloak.test/certs",
        allow_insecure_oidc=True,
        rate_limit_user_per_minute=100_000,
        rate_limit_ip_per_minute=100_000,
    )
    app = create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://api") as api,
    ):
        headers = bearer(mint(row[0]))
        for name, query in QUERIES.items():
            if only and only not in name:
                continue
            url = query if query.startswith("/") else f"/api/v1/map/features?{query}"
            warm = await api.get(url, headers=headers)
            if warm.status_code != 200:
                raise SystemExit(f"{name}: {warm.status_code} {warm.text[:300]}")
            body = warm.json()
            summary = (
                body.get("summary")
                or (
                    {"selected": body["meta"]["record_count"], "shown": 0}
                    if "meta" in body
                    else None
                )
                or {
                    "selected": body.get("quality", body).get("selected", body.get("selected")),
                    "shown": len(body.get("rows", [])),
                }
            )
            times: list[float] = []
            for _ in range(rounds):
                await asyncio.gather(*(timed(api, url, headers, times) for _ in range(concurrency)))
            times.sort()
            p95 = times[max(0, int(len(times) * 0.95) - 1)]
            print(
                f"{name:30s} n={len(times):3d} p50={statistics.median(times):6.0f} ms "
                f"p95={p95:6.0f} ms  selected={summary['selected']} shown={summary['shown']}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--only", help="run only queries whose name contains this text")
    args = parser.parse_args()
    asyncio.run(main(args.concurrency, args.rounds, args.only))
