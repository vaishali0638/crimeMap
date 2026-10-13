"""Prometheus metrics (Phase 8, ADR-038): request counters and latency, plus security and operations
signals derived from the audit log.

* The exposition is plain text (format 0.0.4), written here: no client library is needed for a few
  counters and one histogram.
* Labels never carry identifiers. Routes are recorded by their template (``/api/v1/incidents/{id}``),
  never the raw path, and signals are counts only: no user, IP address, region or record appears.
* ``/metrics`` needs its own bearer token (``CRIMEMAP_METRICS_TOKEN``). Without one it answers 404.
"""

import asyncio
import hmac
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from crimemap_api.deps import PUBLIC, Services
from crimemap_api.http import problem

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 5.0, 10.0)
SIGNAL_TTL_SECONDS = 30.0


@dataclass
class Registry:
    """Per-application counters (one per process; Prometheus sums across replicas)."""

    requests: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))
    buckets: dict[tuple[str, str], list[int]] = field(default_factory=dict)
    sums: dict[tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    counts: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    signals: tuple[float, dict[str, float]] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        # Bounded labels: any other method token shares one series (else memory grows per token).
        method = method if method in METHODS else "OTHER"
        self.requests[(method, route, str(status))] += 1
        key = (method, route)
        counts = self.buckets.setdefault(key, [0] * len(BUCKETS))
        for i, bound in enumerate(BUCKETS):
            if seconds <= bound:
                counts[i] += 1
        self.sums[key] += seconds
        self.counts[key] += 1


def route_label(request: Request) -> str:
    """The matched route template; unmatched paths share one label (no free-text paths)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# Each signal: (metric name, help text, SQL returning one number). Windows are fixed, so a scrape
# cannot be turned into a query of the audit log. All read with the application role.
SIGNALS: Sequence[tuple[str, str, str]] = (
    (
        "crimemap_security_exports_1h",
        "Export requests allowed in the last hour.",
        "SELECT count(*) FROM audit.audit_event WHERE action = 'EXPORT_REQUEST' AND result = 'ALLOW'"
        " AND occurred_at > now() - interval '1 hour'",
    ),
    (
        "crimemap_security_exports_max_per_actor_1h",
        "Most export requests by one user in the last hour (mass-export indicator).",
        "SELECT coalesce(max(n), 0) FROM (SELECT count(*) AS n FROM audit.audit_event"
        " WHERE action = 'EXPORT_REQUEST' AND occurred_at > now() - interval '1 hour'"
        " GROUP BY actor_user_id) t",
    ),
    (
        "crimemap_security_denied_15m",
        "Denied requests (authentication, authorisation, sign-in) in the last 15 minutes.",
        "SELECT count(*) FROM audit.audit_event WHERE result = 'DENY'"
        " AND occurred_at > now() - interval '15 minutes'",
    ),
    (
        "crimemap_security_denied_max_per_source_15m",
        "Most denials from one user or, if unauthenticated, one address in the last 15 minutes.",
        "SELECT coalesce(max(n), 0) FROM (SELECT count(*) AS n FROM audit.audit_event"
        " WHERE result = 'DENY' AND occurred_at > now() - interval '15 minutes'"
        " GROUP BY coalesce(actor_user_id::text, host(ip_address), 'unknown')) t",
    ),
    (
        "crimemap_security_privilege_changes_1h",
        "Role grants and revocations in the last hour.",
        "SELECT count(*) FROM audit.audit_event WHERE action = 'PRIVILEGE_CHANGE'"
        " AND occurred_at > now() - interval '1 hour'",
    ),
    (
        "crimemap_security_config_changes_1h",
        "Configuration changes (AI switches, feature flags) in the last hour.",
        "SELECT count(*) FROM audit.audit_event WHERE action = 'CONFIG_CHANGE'"
        " AND occurred_at > now() - interval '1 hour'",
    ),
    (
        "crimemap_security_new_region_actors_1h",
        "Users who, in the last hour, opened a region they had not used in the previous 30 days"
        " (unusual geographic access indicator).",
        "SELECT count(DISTINCT r.actor_user_id) FROM (SELECT DISTINCT actor_user_id,"
        " filters->>'region' AS region FROM audit.audit_event WHERE result = 'ALLOW'"
        " AND actor_user_id IS NOT NULL AND filters->>'region' IS NOT NULL"
        " AND occurred_at > now() - interval '1 hour') r"
        " WHERE NOT EXISTS (SELECT 1 FROM audit.audit_event p WHERE p.actor_user_id = r.actor_user_id"
        " AND p.filters->>'region' = r.region AND p.result = 'ALLOW'"
        " AND p.occurred_at > now() - interval '30 days' AND p.occurred_at <= now() - interval '1 hour')",
    ),
    (
        "crimemap_ingest_failures_24h",
        "Ingestion batches rejected or failed in the last 24 hours.",
        "SELECT count(*) FROM audit.audit_event WHERE action IN ('INGEST_FAILED', 'INGEST_REJECTED')"
        " AND occurred_at > now() - interval '24 hours'",
    ),
    (
        "crimemap_ingest_stale_sources",
        "Approved or demo sources with no batch in the last 48 hours.",
        "SELECT count(*) FROM core.source_system WHERE approved_status IN ('DEMO', 'APPROVED')"
        " AND (last_batch_at IS NULL OR last_batch_at < now() - interval '48 hours')",
    ),
    (
        "crimemap_ai_run_failures_24h",
        "AI detector runs that failed in the last 24 hours.",
        "SELECT count(*) FROM audit.audit_event WHERE action = 'AI_RUN' AND result = 'ERROR'"
        " AND occurred_at > now() - interval '24 hours'",
    ),
    (
        "crimemap_request_errors_15m",
        "Unhandled request errors recorded in the last 15 minutes.",
        "SELECT count(*) FROM audit.audit_event WHERE action = 'REQUEST_ERROR'"
        " AND occurred_at > now() - interval '15 minutes'",
    ),
)


async def read_signals(engine: AsyncEngine) -> dict[str, float]:
    values: dict[str, float] = {}
    async with asyncio.timeout(5), engine.connect() as conn:
        for name, _, sql in SIGNALS:
            values[name] = float((await conn.execute(text(sql))).scalar_one())
    return values


async def cached_signals(registry: Registry, engine: AsyncEngine) -> dict[str, float] | None:
    """Signals at most ``SIGNAL_TTL_SECONDS`` old; ``None`` when the database cannot be read."""
    async with registry.lock:
        now = time.monotonic()
        if registry.signals is not None and now - registry.signals[0] < SIGNAL_TTL_SECONDS:
            return registry.signals[1]
        try:
            values = await read_signals(engine)
        except Exception:
            return None
        registry.signals = (now, values)
        return values


def render(registry: Registry, signals: dict[str, float] | None) -> str:
    lines = [
        "# HELP crimemap_http_requests_total HTTP requests by method, route template and status.",
        "# TYPE crimemap_http_requests_total counter",
    ]
    for (method, route, status), n in sorted(registry.requests.items()):
        lines.append(
            f'crimemap_http_requests_total{{method="{method}",route="{_escape(route)}",'
            f'status="{status}"}} {n}'
        )
    lines += [
        "# HELP crimemap_http_request_duration_seconds Request latency by method and route template.",
        "# TYPE crimemap_http_request_duration_seconds histogram",
    ]
    for (method, route), counts in sorted(registry.buckets.items()):
        labels = f'method="{method}",route="{_escape(route)}"'
        for bound, n in zip(BUCKETS, counts, strict=True):
            lines.append(
                f'crimemap_http_request_duration_seconds_bucket{{{labels},le="{bound}"}} {n}'
            )
        total = registry.counts[(method, route)]
        lines.append(f'crimemap_http_request_duration_seconds_bucket{{{labels},le="+Inf"}} {total}')
        lines.append(
            f"crimemap_http_request_duration_seconds_sum{{{labels}}} {registry.sums[(method, route)]:.6f}"
        )
        lines.append(f"crimemap_http_request_duration_seconds_count{{{labels}}} {total}")
    lines += [
        "# HELP crimemap_signals_up 1 if the audit-derived signals could be read.",
        "# TYPE crimemap_signals_up gauge",
        f"crimemap_signals_up {0 if signals is None else 1}",
    ]
    for name, help_text, _ in SIGNALS:
        lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
        if signals is not None:
            value = signals[name]
            lines.append(f"{name} {int(value) if value.is_integer() else value!r}")
    return "\n".join(lines) + "\n"


router = APIRouter(tags=["health"])


@router.get("/metrics", dependencies=[PUBLIC], include_in_schema=False)
async def metrics(request: Request) -> Response:
    services: Services = request.app.state.services
    expected = services.settings.metrics_token
    supplied = request.headers.get("authorization", "")
    if expected is None:
        return problem(404)
    if not hmac.compare_digest(supplied.encode(), f"Bearer {expected.get_secret_value()}".encode()):
        return problem(401, headers={"WWW-Authenticate": "Bearer"})
    registry: Registry = request.app.state.metrics
    signals = await cached_signals(registry, services.engine)
    return PlainTextResponse(render(registry, signals), media_type="text/plain; version=0.0.4")
