"""Request IDs, security headers, RFC 9457 problem details, and rate limiting."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from crimemap_api.http import install_http_layer
from crimemap_api.ratelimit import InMemoryRateLimiter


class Body(BaseModel):
    count: int


def build(limit_per_minute: int = 100) -> TestClient:
    app = FastAPI()
    install_http_layer(
        app, rate_limiter=InMemoryRateLimiter(), ip_limit_per_minute=limit_per_minute
    )

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/echo")
    def echo(body: Body) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail: password=hunter2")

    return TestClient(app, raise_server_exceptions=False)


def test_generates_request_id_when_absent() -> None:
    r = build().get("/ok")
    assert uuid.UUID(r.headers["X-Request-ID"])


def test_echoes_valid_request_id() -> None:
    rid = str(uuid.uuid4())
    assert build().get("/ok", headers={"X-Request-ID": rid}).headers["X-Request-ID"] == rid


def test_replaces_invalid_request_id() -> None:
    r = build().get("/ok", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert uuid.UUID(r.headers["X-Request-ID"])


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("Cache-Control", "no-store"),
        ("X-Frame-Options", "DENY"),
    ],
)
def test_security_headers_present(header: str, expected: str) -> None:
    assert build().get("/ok").headers[header] == expected


def test_csp_blocks_framing_and_content() -> None:
    csp = build().get("/ok").headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_not_found_is_problem_json() -> None:
    r = build().get("/missing")
    assert r.status_code == 404
    assert r.headers["Content-Type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 404
    assert body["type"].endswith("/not-found")
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_validation_error_does_not_echo_input() -> None:
    r = build().post("/echo", json={"count": "<script>x</script>"})
    assert r.status_code == 422
    assert r.headers["Content-Type"].startswith("application/problem+json")
    assert "<script>" not in r.text
    assert r.json()["errors"][0]["field"] == "body.count"


def test_unhandled_error_hides_internals() -> None:
    r = build().get("/boom")
    assert r.status_code == 500
    assert "hunter2" not in r.text
    assert "RuntimeError" not in r.text
    assert r.json()["type"].endswith("/internal-error")


def test_rate_limit_returns_429_with_retry_after() -> None:
    client = build(limit_per_minute=3)
    statuses = [client.get("/ok").status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]
    r = client.get("/ok")
    assert r.headers["Content-Type"].startswith("application/problem+json")
    assert int(r.headers["Retry-After"]) > 0
