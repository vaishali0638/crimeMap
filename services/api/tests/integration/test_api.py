"""Phase 1 acceptance at the API: unauthorised calls denied, and login, failed authentication,
denials, and authorised resource views all land in the audit log."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.main import create_app
from crimemap_api.policy.catalogue import Role
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

from ..support import ATTACKER_KEY, ISSUER, JWKS_URL, bearer, local_verifier, mint
from .conftest import Database

pytestmark = pytest.mark.integration


def rid() -> dict[str, str]:
    return {"X-Request-ID": str(uuid.uuid4())}


# --- Public endpoints --------------------------------------------------------------------------


async def test_health_is_public(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "environment": "DEMO"}


async def test_ready_checks_the_database(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/v1/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["database"] is True


# --- Authentication failures are denied and audited -------------------------------------------


async def test_missing_token_is_401_and_audited(api: httpx.AsyncClient, audit_rows) -> None:  # type: ignore[no-untyped-def]
    headers = rid()
    r = await api.get("/api/v1/users", headers=headers)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Bearer")
    assert r.json()["type"].endswith("/unauthenticated")
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["reason"]) == (
        "AUTHN_FAILED",
        "DENY",
        "missing_token",
    )


async def test_forged_token_is_401_and_audited(
    api: httpx.AsyncClient, make_user, audit_rows
) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    headers = {**rid(), **bearer(mint(sub, key=ATTACKER_KEY))}
    r = await api.get("/api/v1/users", headers=headers)
    assert r.status_code == 401
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["reason"]) == ("AUTHN_FAILED", "invalid_signature")
    assert event["actor_user_id"] is None


async def test_repeated_authentication_failures_are_rate_limited(database: Database) -> None:
    settings = Settings(
        database_url=database.app_async,  # type: ignore[arg-type]
        redis_url="redis://unused:6379/0",  # type: ignore[arg-type]
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
        allow_insecure_oidc=True,
        rate_limit_auth_failures_per_minute=3,
    )
    app = create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as c:
        codes = [
            (await c.get("/api/v1/auth/me", headers=bearer("garbage"))).status_code
            for _ in range(5)
        ]
    await app.state.engine.dispose()
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


# --- Authorisation ------------------------------------------------------------------------------


async def test_user_without_permission_is_403_and_audited(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    sub, user_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    headers = {**rid(), **bearer(mint(sub))}
    r = await api.get("/api/v1/users", headers=headers)
    assert r.status_code == 403
    assert r.json()["type"].endswith("/forbidden")
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["reason"]) == (
        "AUTHZ_DENIED",
        "DENY",
        "NO_PERMISSION",
    )
    assert event["actor_user_id"] == user_id
    assert event["after_meta"] == {"permission": "admin.users"}


async def test_privileged_role_without_mfa_is_denied(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    headers = {**rid(), **bearer(mint(sub, mfa=False))}
    r = await api.get("/api/v1/users", headers=headers)
    assert r.status_code == 403
    assert r.json()["type"].endswith("/mfa-required")
    assert audit_rows(headers["X-Request-ID"])[0]["reason"] == "MFA_REQUIRED"


async def test_admin_lists_users_and_the_view_is_audited(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    sub, admin_id = make_user(Role.SYSTEM_ADMIN)
    headers = {**rid(), **bearer(mint(sub))}
    r = await api.get("/api/v1/users", params={"limit": 5}, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) <= 5
    assert set(body["pagination"]) == {"next_cursor", "has_more"}
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["object_type"]) == (
        "RECORD_VIEW",
        "ALLOW",
        "user",
    )
    assert event["actor_user_id"] == admin_id


async def test_user_list_pagination_is_stable(api, make_user) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    for _ in range(3):
        make_user()
    seen: list[str] = []
    cursor = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await api.get("/api/v1/users", params=params, headers=bearer(mint(sub)))).json()
        seen += [u["id"] for u in page["data"]]
        cursor = page["pagination"]["next_cursor"]
        if not page["pagination"]["has_more"]:
            break
    assert len(seen) == len(set(seen)) >= 4


async def test_invalid_cursor_is_rejected(api, make_user) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    r = await api.get("/api/v1/users", params={"cursor": "' OR 1=1--"}, headers=bearer(mint(sub)))
    assert r.status_code == 400


# --- Login and session ---------------------------------------------------------------------------


async def test_login_event_is_audited_and_updates_the_user(
    api, make_user, audit_rows, database
) -> None:  # type: ignore[no-untyped-def]
    sub, user_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    headers = {**rid(), **bearer(mint(sub))}
    r = await api.post("/api/v1/auth/login-events", headers=headers)
    assert r.status_code == 204
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["actor_user_id"]) == ("LOGIN", "ALLOW", user_id)
    assert event["after_meta"]["mfa"] is True
    with psycopg.connect(database.superuser) as conn:
        row = conn.execute(
            "SELECT last_login_at IS NOT NULL, mfa_status FROM core.app_user WHERE id = %s",
            [user_id],
        ).fetchone()
    assert row == (True, "ENROLLED")


async def test_first_request_provisions_a_user_with_no_access(api, audit_rows) -> None:  # type: ignore[no-untyped-def]
    sub = str(uuid.uuid4())
    r = await api.get("/api/v1/auth/me", headers=bearer(mint(sub)))
    assert r.status_code == 200
    assert r.json()["roles"] == []
    r = await api.get("/api/v1/users", headers=bearer(mint(sub)))
    assert r.status_code == 403


async def test_me_reports_roles_scope_and_safeguards(api, make_user) -> None:  # type: ignore[no-untyped-def]
    sub, user_id = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    body = (await api.get("/api/v1/auth/me", headers=bearer(mint(sub)))).json()
    assert body["user_id"] == user_id
    assert body["mfa"] is True
    assert body["environment"] == "DEMO"
    assert body["classification"] == "RESTRICTED"
    assert body["roles"] == [
        {"role": "CRIME_ANALYST", "regions": ["KKL", "PDY"], "station_ids": []}
    ]


# --- Role administration: fresh login, separation of duties, audit ------------------------------


async def test_admin_grants_a_role_and_it_is_audited(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    target_sub, target_id = make_user()
    headers = {**rid(), **bearer(mint(admin_sub))}
    payload = {"role": "DATA_STEWARD", "region_codes": ["MAH"], "reason": "Mahe DQ cover"}
    r = await api.post(f"/api/v1/users/{target_id}/role-grants", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "DATA_STEWARD"
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"]) == ("PRIVILEGE_CHANGE", "ALLOW")
    assert event["after_meta"]["role"] == "DATA_STEWARD"
    me = (await api.get("/api/v1/auth/me", headers=bearer(mint(target_sub)))).json()
    assert me["roles"][0]["regions"] == ["MAH"]


async def test_admin_cannot_grant_a_role_to_themselves(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    admin_sub, admin_id = make_user(Role.SYSTEM_ADMIN)
    headers = {**rid(), **bearer(mint(admin_sub))}
    payload = {"role": "CRIME_ANALYST", "region_codes": ["PDY"], "reason": "self"}
    r = await api.post(f"/api/v1/users/{admin_id}/role-grants", json=payload, headers=headers)
    assert r.status_code == 409
    assert r.json()["type"].endswith("/separation-of-duties")
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["reason"]) == (
        "PRIVILEGE_CHANGE",
        "DENY",
        "SEPARATION_OF_DUTIES",
    )


async def test_role_change_requires_a_fresh_login(api, make_user) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    _, target_id = make_user()
    token = mint(admin_sub, auth_age=timedelta(minutes=30))
    payload = {"role": "AUDITOR", "region_codes": [], "reason": "audit cover"}
    r = await api.post(
        f"/api/v1/users/{target_id}/role-grants", json=payload, headers=bearer(token)
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("/reauthentication-required")


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "ROOT", "region_codes": [], "reason": "nope"},
        {"role": "CRIME_ANALYST", "region_codes": ["XYZ"], "reason": "bad region"},
        {"role": "CRIME_ANALYST", "region_codes": ["PDY"], "reason": "x", "is_admin": True},
        {"role": "CRIME_ANALYST", "region_codes": ["PDY"]},
    ],
)
async def test_invalid_grant_requests_are_rejected(api, make_user, payload) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    _, target_id = make_user()
    r = await api.post(
        f"/api/v1/users/{target_id}/role-grants", json=payload, headers=bearer(mint(admin_sub))
    )
    assert r.status_code == 422


async def test_grant_for_unknown_user_is_404(api, make_user) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    payload = {"role": "AUDITOR", "region_codes": [], "reason": "cover"}
    r = await api.post(
        f"/api/v1/users/{uuid.uuid4()}/role-grants", json=payload, headers=bearer(mint(admin_sub))
    )
    assert r.status_code == 404


async def test_admin_revokes_a_grant(api, make_user, audit_rows) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    target_sub, target_id = make_user(Role.AUDITOR)
    grants = (
        await api.get(f"/api/v1/users/{target_id}/role-grants", headers=bearer(mint(admin_sub)))
    ).json()
    grant_id = grants["data"][0]["id"]
    headers = {**rid(), **bearer(mint(admin_sub))}
    r = await api.post(
        f"/api/v1/users/{target_id}/role-grants/{grant_id}/revocations",
        json={"reason": "left the unit"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None
    assert audit_rows(headers["X-Request-ID"])[0]["action"] == "PRIVILEGE_CHANGE"
    me = (await api.get("/api/v1/auth/me", headers=bearer(mint(target_sub)))).json()
    assert me["roles"] == []


# --- Audit trail access ---------------------------------------------------------------------------


async def test_auditor_reads_the_audit_trail_and_that_is_audited(
    api, make_user, audit_rows
) -> None:  # type: ignore[no-untyped-def]
    auditor_sub, _ = make_user(Role.AUDITOR)
    headers = {**rid(), **bearer(mint(auditor_sub))}
    r = await api.get("/api/v1/audit/events", params={"limit": 10}, headers=headers)
    assert r.status_code == 200
    assert 0 < len(r.json()["data"]) <= 10
    assert "after_meta" in r.json()["data"][0]
    assert audit_rows(headers["X-Request-ID"])[0]["action"] == "AUDIT_VIEW"


async def test_system_admin_sees_audit_metadata_only(api, make_user) -> None:  # type: ignore[no-untyped-def]
    admin_sub, _ = make_user(Role.SYSTEM_ADMIN)
    r = await api.get("/api/v1/audit/events", params={"limit": 5}, headers=bearer(mint(admin_sub)))
    assert r.status_code == 200
    for event in r.json()["data"]:
        assert {"before_meta", "after_meta", "filters"}.isdisjoint(event)


async def test_audit_chain_verification_endpoint(api, make_user) -> None:  # type: ignore[no-untyped-def]
    auditor_sub, _ = make_user(Role.AUDITOR)
    r = await api.get("/api/v1/audit/verification", headers=bearer(mint(auditor_sub)))
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_analyst_cannot_read_the_audit_trail(api, make_user) -> None:  # type: ignore[no-untyped-def]
    sub, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    assert (await api.get("/api/v1/audit/events", headers=bearer(mint(sub)))).status_code == 403


# --- Failure inside a request is still audited ------------------------------------------------


async def test_error_inside_a_request_is_audited(api, make_user, audit_rows, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from crimemap_api.identity import store

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database exploded: secret=hunter2")

    monkeypatch.setattr(store, "list_users", broken)
    sub, _ = make_user(Role.SYSTEM_ADMIN)
    headers = {**rid(), **bearer(mint(sub))}
    r = await api.get("/api/v1/users", headers=headers)
    assert r.status_code == 500
    assert "hunter2" not in r.text
    [event] = audit_rows(headers["X-Request-ID"])
    assert (event["action"], event["result"], event["reason"]) == (
        "REQUEST_ERROR",
        "ERROR",
        "RuntimeError",
    )


# --- Every route declares its authorisation requirement ---------------------------------------


async def test_every_route_declares_a_permission(api: httpx.AsyncClient) -> None:
    from crimemap_api.deps import route_requirements

    requirements = route_requirements(api.app)  # type: ignore[attr-defined]
    assert requirements, "no routes found"
    assert all(req is not None for req in requirements.values()), requirements


# --- Grant validity uses the database clock (found in Phase 1: VM clock ahead of host) ------------


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "expected"),
    [
        ("now()", None, 200),  # just granted: usable immediately even if clocks differ slightly
        ("now() + interval '1 hour'", None, 403),  # scheduled grant: not active yet
        ("now() - interval '2 hours'", "now() - interval '1 hour'", 403),  # expired grant
    ],
)
async def test_grant_validity_is_decided_by_the_database_clock(
    api, database, valid_from, valid_to, expected
) -> None:  # type: ignore[no-untyped-def]
    sub = str(uuid.uuid4())
    with psycopg.connect(database.migrator, autocommit=True) as conn:
        uid = conn.execute(
            "INSERT INTO core.app_user (idp_subject) VALUES (%s) RETURNING id", [sub]
        ).fetchone()[0]  # type: ignore[index]
        conn.execute(
            "INSERT INTO core.user_role_grant (user_id, role_code, valid_from, valid_to, grant_reason) "
            f"VALUES (%s, 'AUDITOR', {valid_from}, {valid_to or 'NULL'}, 'clock test')",
            [uid],
        )
    r = await api.get("/api/v1/audit/verification", headers=bearer(mint(sub)))
    assert r.status_code == expected
