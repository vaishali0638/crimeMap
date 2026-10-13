"""Phase 8 security tests (spec QA: auth bypass, IDOR, injection, XSS, CSRF, SSRF, rate limit,
malicious upload, scope escape; authorisation for each role).

Malicious uploads are in tests/unit/test_malicious_batches.py and test_adapters.py (batch files are
the only file input). Earlier phases test individual cases in depth; these tests sweep every route.
"""

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from jwt.utils import base64url_encode

from crimemap_api.deps import Requirement, route_requirements
from crimemap_api.main import create_app
from crimemap_api.policy.catalogue import ROLE_PERMISSIONS, Role
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

from ..support import (
    ATTACKER_KEY,
    AUDIENCE,
    CLIENT,
    ISSUER,
    JWKS_URL,
    KID,
    SIGNING_KEY,
    bearer,
    local_verifier,
    mint,
)
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

SAMPLE = {
    "station_code": "PDY-S01",
    "board": "week",
    "unit_code": "PDY",
    "code": "DEMO_SYNTHETIC",
}


def concrete(path: str) -> str:
    """Fill path parameters: known codes where the route takes a code, random UUIDs otherwise."""
    return re.sub(r"\{(\w+)\}", lambda m: SAMPLE.get(m.group(1), str(uuid.uuid4())), path)


def routes(api: httpx.AsyncClient) -> dict[str, object | None]:
    return route_requirements(api.app)  # type: ignore[attr-defined]


async def call(api: httpx.AsyncClient, key: str, headers: dict[str, str]) -> httpx.Response:
    method, path = key.split(" ", 1)
    url = concrete(path)
    if method in {"POST", "PATCH", "PUT"}:
        return await api.request(method, url, headers=headers, json={})
    return await api.request(method, url, headers=headers)


def audit_actions(database: Database, request_id: str) -> list[tuple[str, str]]:
    with psycopg.connect(database.superuser) as conn:
        return [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT action, result FROM audit.audit_event WHERE request_id = %s ORDER BY seq",
                [request_id],
            ).fetchall()
        ]


def audit_denials(database: Database, request_id: str) -> list[str]:
    with psycopg.connect(database.superuser) as conn:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT reason FROM audit.audit_event WHERE request_id = %s AND action = 'AUTHZ_DENIED'",
                [request_id],
            ).fetchall()
        ]


# --- Authentication bypass ------------------------------------------------------------------------


async def test_every_non_public_route_refuses_an_anonymous_caller(api: httpx.AsyncClient) -> None:
    checked = 0
    for key, requirement in routes(api).items():
        if requirement is not None and not isinstance(requirement, Requirement):
            continue  # PUBLIC: health, readiness, metrics (own token)
        response = await call(api, key, {})
        assert response.status_code == 401, key
        checked += 1
    assert checked > 70


def _claims(subject: str, **override: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": [AUDIENCE],
        "azp": CLIENT,
        "sub": subject,
        "typ": "Bearer",
        "iat": int(now.timestamp()) - 5,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "auth_time": int(now.timestamp()) - 10,
        "amr": ["pwd", "otp"],
    }
    claims.update(override)
    return {k: v for k, v in claims.items() if v is not None}


def _rs256(claims: dict[str, Any], key: Any = SIGNING_KEY) -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def _hs256_with_public_key(claims: dict[str, Any]) -> str:
    """Algorithm confusion: an HMAC token keyed with the identity provider's public key."""
    public = SIGNING_KEY.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    header = base64url_encode(json.dumps({"alg": "HS256", "kid": KID, "typ": "JWT"}).encode())
    body = base64url_encode(json.dumps(claims).encode())
    signature = hmac.new(public, header + b"." + body, hashlib.sha256).digest()
    return b".".join((header, body, base64url_encode(signature))).decode()


def _forged(kind: str, sub: str) -> str:
    past = int(datetime.now(UTC).timestamp()) - 60
    match kind:
        case "alg-none":
            return jwt.encode(_claims(sub), None, algorithm="none", headers={"kid": KID})  # type: ignore[arg-type]
        case "hs256-with-public-key":
            return _hs256_with_public_key(_claims(sub))
        case "attacker-key":
            return _rs256(_claims(sub), ATTACKER_KEY)
        case "expired":
            return _rs256(_claims(sub, exp=past))
        case "wrong-issuer":
            return _rs256(_claims(sub, iss="http://evil.test/realms/x"))
        case "wrong-audience":
            return _rs256(_claims(sub, aud=["account"]))
        case "foreign-client":
            return _rs256(_claims(sub, azp="some-other-client"))
        case "no-subject":
            return _rs256(_claims(sub, sub=None))
        case "id-token-type":
            return _rs256(_claims(sub, typ="ID"))
        case "garbage":
            return "not.a.jwt"
    raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "alg-none",
        "hs256-with-public-key",
        "attacker-key",
        "expired",
        "wrong-issuer",
        "wrong-audience",
        "foreign-client",
        "no-subject",
        "id-token-type",
        "garbage",
    ],
)
async def test_forged_or_unacceptable_tokens_are_refused_and_audited(
    api: httpx.AsyncClient, make_user, database: Database, kind: str
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    request_id = str(uuid.uuid4())
    response = await api.get(
        "/api/v1/users", headers={**bearer(_forged(kind, sub)), "X-Request-ID": request_id}
    )
    assert response.status_code == 401, kind
    assert ("AUTHN_FAILED", "DENY") in audit_actions(database, request_id)


async def test_a_session_cookie_is_not_a_credential(api: httpx.AsyncClient, make_user) -> None:  # type: ignore[no-untyped-def]
    # CSRF: the API reads only the Authorization header, so a browser's ambient cookies cannot
    # authorise a cross-site request. (The web BFF's own CSRF checks: apps/web/tests/csrf.test.ts.)
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    token = mint(sub)
    for cookie in (f"session={token}", f"access_token={token}", f"Authorization=Bearer {token}"):
        response = await api.post(
            "/api/v1/notes",
            headers={"Origin": "https://evil.test", "Cookie": cookie},
            json={
                "subject_type": "INCIDENT",
                "subject_id": str(uuid.uuid4()),
                "note": "x",
                "visibility": "INTERNAL",
            },
        )
        assert response.status_code == 401
        assert "access-control-allow-origin" not in response.headers


# --- Authorisation for each role ------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
async def test_each_role_is_refused_every_permission_it_lacks(
    api: httpx.AsyncClient, make_user, database: Database, role: Role
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(role, regions=("PDY",), stations=("PDY-S01",))
    headers = bearer(mint(sub))
    granted = set(ROLE_PERMISSIONS[role])
    refused = allowed = 0
    for key, requirement in routes(api).items():
        if not isinstance(requirement, Requirement) or requirement.permission is None:
            continue  # PUBLIC, or any signed-in user (checked per item in the handler)
        request_id = str(uuid.uuid4())
        response = await call(api, key, {**headers, "X-Request-ID": request_id})
        assert response.status_code not in (401, 500), (role, key, response.status_code)
        denials = audit_denials(database, request_id)
        if requirement.permission in granted:
            # Granted: never refused for lacking the permission (territory, validation or a
            # missing object may still answer 403/404/409/422).
            assert "NO_PERMISSION" not in denials, (role, key)
            allowed += 1
        else:
            assert response.status_code == 403, (role, key, response.status_code)
            assert "NO_PERMISSION" in denials, (role, key, denials)
            refused += 1
    assert refused + allowed > 50


# --- IDOR and scope escape ------------------------------------------------------------------------


def _incidents(database: Database) -> dict[str, str]:
    """One visible (non-sensitive, not highly restricted) incident per station code."""
    with psycopg.connect(database.superuser) as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (s.code) s.code, i.id::text FROM core.incident_record i "
            "JOIN core.organizational_unit s ON s.id = i.station_id "
            "WHERE NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
            "ORDER BY s.code, i.id"
        ).fetchall()
    return {code: rid for code, rid in rows}


async def test_a_station_officer_cannot_open_another_stations_record(
    api: httpx.AsyncClient, make_user, ingested: Ingested, database: Database
) -> None:  # type: ignore[no-untyped-def]
    incidents = _incidents(database)
    own = "PDY-S01"
    other = next(code for code in incidents if code != own and code.startswith("PDY"))
    foreign = next(code for code in incidents if not code.startswith("PDY"))
    sub, _ = make_user(Role.STATION_OFFICER, stations=(own,))
    headers = bearer(mint(sub))
    assert (
        await api.get(f"/api/v1/incidents/{incidents[own]}/summary", headers=headers)
    ).status_code == 200
    for code in (other, foreign):
        for path in ("/summary", ""):
            response = await api.get(f"/api/v1/incidents/{incidents[code]}{path}", headers=headers)
            # 404, not 403: the response must not confirm that the record exists.
            assert response.status_code == 404, (code, path)
    # A guessed identifier answers exactly like another station's record.
    guessed = await api.get(f"/api/v1/incidents/{uuid.uuid4()}/summary", headers=headers)
    assert guessed.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/map/features?region=KKL&aggregation=BEAT&period_from=2026-01-01&period_to=2026-03-31",
        "/api/v1/analytics/overview?region=KKL&period_from=2026-01-01&period_to=2026-03-31",
        "/api/v1/dashboard/stations/KKL-S01?period_from=2026-01-01&period_to=2026-03-31",
        "/api/v1/map/features?region=PDY&station=KKL-S01&aggregation=BEAT&period_from=2026-01-01&period_to=2026-03-31",
    ],
)
async def test_a_region_scoped_analyst_cannot_escape_their_region(
    api: httpx.AsyncClient, make_user, ingested: Ingested, database: Database, path: str
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.CRIME_ANALYST, regions=("PDY",))
    request_id = str(uuid.uuid4())
    response = await api.get(path, headers={**bearer(mint(sub)), "X-Request-ID": request_id})
    assert response.status_code in (403, 404, 422), response.text
    assert "KKL-S01" not in response.text or response.status_code == 422
    if response.status_code == 403:
        assert ("AUTHZ_DENIED", "DENY") in audit_actions(database, request_id)


# --- Injection and XSS ----------------------------------------------------------------------------

PAYLOADS = [
    "' OR 1=1--",
    "PDY'; DROP TABLE core.incident_record;--",
    "1 UNION SELECT idp_subject FROM core.app_user",
    "$$; SELECT pg_sleep(5); $$",
    "<script>alert(1)</script>",
    "../../etc/passwd",
    "%00",
]


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_injection_payloads_are_rejected_or_treated_as_data(
    api: httpx.AsyncClient, make_user, ingested: Ingested, database: Database, payload: str
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.CRIME_ANALYST, regions=("PDY",))
    headers = bearer(mint(sub))
    with psycopg.connect(database.superuser) as conn:
        before = conn.execute("SELECT count(*) FROM core.incident_record").fetchone()
    probes = [
        ("/api/v1/search", {"q": payload, "region": "PDY"}),
        ("/api/v1/map/features", {"region": payload, "aggregation": "BEAT"}),
        (
            "/api/v1/map/features",
            {
                "region": "PDY",
                "category": payload,
                "aggregation": "BEAT",
                "period_from": "2026-01-01",
                "period_to": "2026-03-31",
            },
        ),
        (
            "/api/v1/analytics/overview",
            {
                "region": "PDY",
                "station": payload,
                "period_from": "2026-01-01",
                "period_to": "2026-03-31",
            },
        ),
        ("/api/v1/dq/issues", {"state": payload}),
        ("/api/v1/review-queue", {"tab": payload}),
    ]
    for path, params in probes:
        response = await api.get(path, params=params, headers=headers)
        assert response.status_code < 500, (path, params, response.status_code)
        assert response.headers["content-type"].startswith(
            ("application/json", "application/problem+json")
        )
        # Validation errors carry field paths, never the submitted value. (A search may return
        # its query as JSON data: served as JSON with nosniff and CSP 'none', rendered as text.)
        if response.status_code == 422:
            assert payload not in response.text, path
    path_probe = await api.get(f"/api/v1/incidents/{payload}/summary", headers=headers)
    assert path_probe.status_code in (404, 422)
    with psycopg.connect(database.superuser) as conn:
        assert conn.execute("SELECT count(*) FROM core.incident_record").fetchone() == before


async def test_stored_markup_is_returned_as_inert_json(
    api: httpx.AsyncClient, make_user, ingested: Ingested, database: Database
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.CRIME_ANALYST, regions=("PDY",))
    headers = bearer(mint(sub))
    incident = _incidents(database)["PDY-S01"]
    markup = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    created = await api.post(
        "/api/v1/notes",
        headers=headers,
        json={
            "subject_type": "INCIDENT",
            "subject_id": incident,
            "note": markup,
            "visibility": "INTERNAL",
        },
    )
    assert created.status_code == 201, created.text
    listed = await api.get(
        "/api/v1/notes",
        params={"subject_type": "INCIDENT", "subject_id": incident},
        headers=headers,
    )
    assert listed.status_code == 200
    # Stored as text, returned as JSON data (rendered as text by React, never as HTML).
    assert markup in [n["note"] for n in listed.json()["data"]]
    for response in (created, listed):
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in response.headers["content-security-policy"]


# --- SSRF -----------------------------------------------------------------------------------------

URLISH = re.compile(r"(url|uri|href|link|callback|webhook|endpoint|host|redirect)", re.I)


async def test_no_endpoint_accepts_a_url_or_host_to_fetch(api: httpx.AsyncClient) -> None:
    # The API makes one outbound request, to the configured JWKS URL (settings only, redirects
    # off). No request parameter or body field may name a URL or host for the server to fetch.
    spec = api.app.openapi()  # type: ignore[attr-defined]
    schemas = spec["components"]["schemas"]
    found: list[str] = []
    seen: set[str] = set()

    def walk(node: object, where: str) -> None:
        # Every field of every request body, following $ref into nested models (Phase 8 audit:
        # filtering schemas by name missed IssueUpdate, ReviewEdit, OutcomeEdit and nested ones).
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.split("/")[-1]
                if name not in seen:
                    seen.add(name)
                    walk(schemas[name], name)
                return
            if node.get("format") == "uri":
                found.append(where)
            for field, prop in (node.get("properties") or {}).items():
                if URLISH.search(field):
                    found.append(f"{where}.{field}")
                walk(prop, f"{where}.{field}")
            for key in ("items", "anyOf", "oneOf", "allOf", "additionalProperties"):
                walk(node.get(key), where)
        elif isinstance(node, list):
            for item in node:
                walk(item, where)

    bodies = 0
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            for param in op.get("parameters", []):
                if URLISH.search(param["name"]):
                    found.append(f"{method.upper()} {path} ?{param['name']}")
                walk(param.get("schema"), f"{method.upper()} {path} ?{param['name']}")
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
            if body:
                bodies += 1
                walk(body.get("schema"), f"{method.upper()} {path} body")
    assert bodies >= 20
    assert {"IssueUpdate", "ReviewEdit", "OutcomeEdit", "AnalyticsQuery"} <= seen
    assert found == []
    services = api.app.state.services  # type: ignore[attr-defined]
    assert services.jwks is None  # tests inject a local verifier; production builds it below
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://u:p@127.0.0.1:1/x",  # type: ignore[arg-type]
            redis_url="redis://localhost/0",  # type: ignore[arg-type]
            oidc_issuer=ISSUER,
            oidc_jwks_url=JWKS_URL,
            allow_insecure_oidc=True,
        )
    )
    jwks = app.state.services.jwks
    assert jwks is not None
    assert jwks._url == JWKS_URL
    assert jwks._client.follow_redirects is False


# --- Rate limits ----------------------------------------------------------------------------------


async def test_a_user_over_the_rate_limit_gets_429(database: Database, make_user) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        database_url=database.app_async,  # type: ignore[arg-type]
        redis_url="redis://unused:6379/0",  # type: ignore[arg-type]
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
        allow_insecure_oidc=True,
        rate_limit_user_per_minute=3,
    )
    app = create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    sub, _ = make_user(Role.CRIME_ANALYST, regions=("PDY",))
    headers = bearer(mint(sub))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as client:
        statuses = [
            (await client.get("/api/v1/auth/me", headers=headers)).status_code for _ in range(5)
        ]
        limited = await client.get("/api/v1/auth/me", headers=headers)
    await app.state.engine.dispose()
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3:] == [429, 429]
    assert int(limited.headers["Retry-After"]) > 0
