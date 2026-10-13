"""/metrics: token-protected, no identifiers in labels, audit-derived signals (Phase 8)."""

import uuid

import httpx
import pytest

from crimemap_api.main import create_app
from crimemap_api.metrics import SIGNALS, Registry, render
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

from ..support import ISSUER, JWKS_URL, local_verifier

TOKEN = "t" * 40


def settings(**extra: object) -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@127.0.0.1:1/x",  # type: ignore[arg-type]
        redis_url="redis://localhost/0",  # type: ignore[arg-type]
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
        allow_insecure_oidc=True,
        **extra,  # type: ignore[arg-type]
    )


def client(s: Settings) -> httpx.AsyncClient:
    app = create_app(s, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://api"
    )


async def test_metrics_are_not_served_without_a_configured_token() -> None:
    async with client(settings()) as api:
        response = await api.get("/metrics", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer wrong", f"Bearer {TOKEN}x", TOKEN])
async def test_metrics_need_the_exact_bearer_token(header: str | None) -> None:
    headers = {"Authorization": header} if header else {}
    async with client(settings(metrics_token=TOKEN)) as api:
        response = await api.get("/metrics", headers=headers)
    assert response.status_code == 401
    assert "crimemap_http" not in response.text


def test_an_empty_metrics_token_means_unset() -> None:
    # Compose passes CRIMEMAP_METRICS_TOKEN="" when .env has none.
    assert settings(metrics_token="").metrics_token is None


def test_a_short_metrics_token_is_refused() -> None:
    with pytest.raises(ValueError, match="metrics_token"):
        settings(metrics_token="short")


async def test_routes_are_labelled_by_template_never_by_identifier() -> None:
    record = str(uuid.uuid4())
    async with client(settings(metrics_token=TOKEN)) as api:
        await api.get("/api/v1/health")
        # No database here, so the denial's audit write fails (500): counted under the template.
        await api.get(f"/api/v1/incidents/{record}")
        await api.get(f"/no/such/{record}")
        body = (await api.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    assert record not in body
    assert (
        'crimemap_http_requests_total{method="GET",route="/api/v1/health",status="200"} 1' in body
    )
    assert 'route="/api/v1/incidents/{incident_id}",status="500"} 1' in body
    assert 'route="unmatched",status="404"} 1' in body
    assert (
        'crimemap_http_request_duration_seconds_bucket{method="GET",route="/api/v1/health",le="+Inf"} 1'
        in body
    )


async def test_signals_are_reported_down_when_the_database_cannot_be_read() -> None:
    async with client(settings(metrics_token=TOKEN)) as api:
        body = (await api.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    assert "crimemap_signals_up 0" in body
    # Families are still declared, so alert rules see "no data" rather than a missing metric.
    for name, _, _ in SIGNALS:
        assert f"# TYPE {name} gauge" in body


def test_histogram_buckets_are_cumulative() -> None:
    registry = Registry()
    for seconds in (0.01, 0.3, 0.3, 4.0, 20.0):
        registry.observe("GET", "/x", 200, seconds)
    body = render(registry, {name: 0.0 for name, _, _ in SIGNALS})
    assert 'le="0.05"} 1' in body
    assert 'le="0.5"} 3' in body
    assert 'le="5.0"} 4' in body
    assert 'le="+Inf"} 5' in body
    assert "crimemap_signals_up 1" in body


def test_large_counts_are_exported_exactly() -> None:
    # Phase 8 audit: "%g" rounded 1,234,567 to 1.23457e+06.
    body = render(Registry(), {name: 1_234_567.0 for name, _, _ in SIGNALS})
    assert "crimemap_security_denied_15m 1234567\n" in body


async def test_unknown_http_methods_share_one_label() -> None:
    # Phase 8 audit: any method token became a new series (unbounded memory).
    async with client(settings(metrics_token=TOKEN)) as api:
        for i in range(5):
            await api.request(f"X{i}", "/api/v1/health")
        body = (await api.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})).text
    assert 'method="X0"' not in body
    assert body.count('method="OTHER"') >= 1
    assert (
        len(
            [
                line
                for line in body.splitlines()
                if line.startswith('crimemap_http_requests_total{method="OTHER"')
            ]
        )
        == 1
    )
