"""Phase 2 at the API: synthetic geography, the data-quality queue (territory-scoped, audited state
changes), source health and reconciliation, and dual-control boundary approval."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

DQ_VIEW_AUDIT_1 = str(uuid.uuid4())
DQ_RESOLVE_1 = str(uuid.uuid4())
DQ_CROSS_REGION_1 = str(uuid.uuid4())
BOUNDARY_APPROVE_2 = str(uuid.uuid4())
MakeUser = Callable[..., tuple[str, str]]


def add_grant(database: Database, user_id: str, role: Role, regions: tuple[str, ...]) -> None:
    with psycopg.connect(database.migrator, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO core.user_role_grant (user_id, role_code, scope_region_ids, grant_reason) "
            "SELECT %s, %s, array_agg(id), 'test' FROM core.organizational_unit "
            "WHERE unit_type = 'REGION' AND code = ANY(%s)",
            [user_id, role.value, list(regions)],
        )


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


# --- Geography ----------------------------------------------------------------------------------


async def test_regions_stations_and_beats_are_listed_and_marked_demo(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DEMO_USER)
    regions = (await api.get("/api/v1/regions", headers=as_user(sub))).json()["data"]
    assert [r["code"] for r in regions] == ["KKL", "MAH", "PDY", "YAN"]
    assert {r["station_count"] for r in regions if r["code"] == "PDY"} == {10}
    stations = (await api.get("/api/v1/stations?region=MAH", headers=as_user(sub))).json()["data"]
    assert [s["code"] for s in stations] == ["MAH-S01", "MAH-S02"]
    assert all(s["is_demo"] and s["region_code"] == "MAH" for s in stations)
    beats = (await api.get("/api/v1/beats?station=MAH-S01", headers=as_user(sub))).json()["data"]
    assert 2 <= len(beats) <= 4
    assert all(b["station_code"] == "MAH-S01" for b in beats)


async def test_boundaries_are_geojson_labelled_synthetic(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DEMO_USER)
    r = await api.get("/api/v1/boundaries?level=REGION", headers=as_user(sub))
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["data_label"] == "DEMO / SYNTHETIC DATA"
    assert len(body["features"]) == 4
    feature = body["features"][0]
    assert feature["geometry"]["type"] == "MultiPolygon"
    assert feature["properties"]["approval_status"] == "DEMO_ONLY"
    assert "SYNTHETIC" in feature["properties"]["source"]


async def test_geography_requires_authentication(api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/v1/regions")).status_code == 401


async def test_unknown_level_is_rejected(api: httpx.AsyncClient, make_user: MakeUser) -> None:
    sub, _ = make_user(Role.DEMO_USER)
    r = await api.get("/api/v1/boundaries?level=DISTRICT", headers=as_user(sub))
    assert r.status_code == 422


# --- Data-quality queue -------------------------------------------------------------------------


async def test_steward_sees_only_issues_in_their_region_plus_source_issues(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("KKL",))
    r = await api.get("/api/v1/dq/issues?limit=100&state=OPEN", headers=as_user(sub))
    assert r.status_code == 200
    items = r.json()["data"]
    assert items
    assert {i["region_code"] for i in items if i["subject_type"] == "INCIDENT"} == {"KKL"}
    assert all(i["subject_type"] in ("INCIDENT", "SOURCE_SYSTEM") for i in items)


async def test_queue_is_limited_to_the_territory_of_the_permission_not_all_grants(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, user_id = make_user(Role.DATA_STEWARD, ("KKL",))
    add_grant(ingested.database, user_id, Role.CRIME_ANALYST, ("PDY",))
    items = []
    cursor = None
    for _ in range(50):
        q = "/api/v1/dq/issues?limit=100" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(q, headers=as_user(sub))).json()
        items += page["data"]
        cursor = page["pagination"]["next_cursor"]
        if not page["pagination"]["has_more"]:
            break
    regions = {i["region_code"] for i in items if i["subject_type"] == "INCIDENT"}
    assert regions == {"KKL"}


async def test_pagination_returns_each_issue_once(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("MAH",))
    seen: list[str] = []
    cursor = None
    while True:
        q = "/api/v1/dq/issues?limit=7" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(q, headers=as_user(sub))).json()
        seen += [i["id"] for i in page["data"]]
        cursor = page["pagination"]["next_cursor"]
        if not page["pagination"]["has_more"]:
            break
    assert len(seen) == len(set(seen))
    total = (await api.get("/api/v1/dq/summary", headers=as_user(sub))).json()["total"]
    assert len(seen) == total


async def test_queue_hides_sensitive_categories_and_exact_locations_from_the_steward(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    page = (await api.get("/api/v1/dq/issues?limit=100", headers=as_user(sub))).json()
    incidents = [i["incident"] for i in page["data"] if i["incident"]]
    assert incidents
    for inc in incidents:
        assert "lat" not in inc
        assert "lon" not in inc
        assert "geometry" not in inc
        # a steward without sensitive-category access sees no category on any incident, so the
        # sensitive cases cannot be told apart (audit F1)
        assert inc["category_code"] is None
        assert "category_restricted" not in inc
    assert page["categories_shown"] is False


async def test_queue_view_is_audited_with_filters(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("YAN",))
    headers = {**as_user(sub), "X-Request-ID": DQ_VIEW_AUDIT_1}
    await api.get("/api/v1/dq/issues?severity=HIGH", headers=headers)
    rows = audit_rows(DQ_VIEW_AUDIT_1)
    assert [(r["action"], r["object_type"]) for r in rows] == [("RECORD_VIEW", "dq_issue")]
    assert rows[0]["after_meta"]["count"] >= 0


async def test_roles_without_dq_manage_are_denied(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    sub, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    assert (await api.get("/api/v1/dq/issues", headers=as_user(sub))).status_code == 403


async def _first_open_issue(api: httpx.AsyncClient, sub: str) -> dict[str, Any]:
    q = "/api/v1/dq/issues?state=OPEN&subject_type=INCIDENT&limit=1"
    page = (await api.get(q, headers=as_user(sub))).json()
    return page["data"][0]  # type: ignore[no-any-return]


async def test_steward_assigns_and_resolves_an_issue_and_it_is_audited(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    sub, user_id = make_user(Role.DATA_STEWARD, ("KKL",))
    issue = await _first_open_issue(api, sub)
    r = await api.patch(
        f"/api/v1/dq/issues/{issue['id']}", json={"state": "ASSIGNED"}, headers=as_user(sub)
    )
    assert r.status_code == 200
    assert r.json()["state"] == "ASSIGNED"
    assert r.json()["assigned_to"] == user_id
    headers = {**as_user(sub), "X-Request-ID": DQ_RESOLVE_1}
    r = await api.patch(
        f"/api/v1/dq/issues/{issue['id']}",
        json={"state": "RESOLVED", "resolution_note": "Checked against the station register."},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["state"] == "RESOLVED"
    rows = audit_rows(DQ_RESOLVE_1)
    assert [r["action"] for r in rows] == ["DQ_ISSUE_UPDATE"]
    assert rows[0]["before_meta"]["state"] == "ASSIGNED"
    assert rows[0]["after_meta"]["state"] == "RESOLVED"
    assert "Checked" not in str(rows[0]["after_meta"])  # free text stays out of the audit log


async def test_resolution_requires_a_note(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("KKL",))
    issue = await _first_open_issue(api, sub)
    r = await api.patch(
        f"/api/v1/dq/issues/{issue['id']}", json={"state": "RESOLVED"}, headers=as_user(sub)
    )
    assert r.status_code == 422


async def test_invalid_transition_is_rejected(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("KKL",))
    issue = await _first_open_issue(api, sub)
    r = await api.patch(
        f"/api/v1/dq/issues/{issue['id']}", json={"state": "OPEN"}, headers=as_user(sub)
    )
    assert r.status_code == 409


async def test_steward_cannot_change_an_issue_in_another_region(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    pdy, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    kkl, _ = make_user(Role.DATA_STEWARD, ("KKL",))
    issue = await _first_open_issue(api, pdy)
    headers = {**as_user(kkl), "X-Request-ID": DQ_CROSS_REGION_1}
    r = await api.patch(
        f"/api/v1/dq/issues/{issue['id']}", json={"state": "ASSIGNED"}, headers=headers
    )
    assert r.status_code == 404  # not visible at all, so existence is not disclosed
    rows = audit_rows(DQ_CROSS_REGION_1)
    assert [(x["action"], x["result"], x["reason"]) for x in rows] == [
        ("DQ_ISSUE_UPDATE", "DENY", "NOT_FOUND_IN_SCOPE")
    ]


async def test_summary_counts_by_type_and_severity(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    body = (await api.get("/api/v1/dq/summary", headers=as_user(sub))).json()
    assert body["total"] == sum(row["count"] for row in body["by_type"])
    assert {row["issue_type"] for row in body["by_type"]} >= {"MISSING_LOCATION"}
    assert set(body["by_state"]) <= {"OPEN", "ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"}


# --- Integrations: source health and reconciliation ---------------------------------------------


async def test_source_health_lists_demo_feeds_and_disabled_stubs(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.AUDITOR)
    r = await api.get("/api/v1/integrations/sources", headers=as_user(sub))
    assert r.status_code == 200
    by_code = {s["code"]: s for s in r.json()["data"]}
    assert by_code["DEMO_SYNTHETIC"]["approved_status"] == "DEMO"
    last = by_code["DEMO_SYNTHETIC"]["last_batch"]
    assert last["status"] == "PROCESSED"
    assert last["record_count"] == last["accepted"] + last["duplicates"] + last["rejected"]
    assert by_code["CCTNS"]["approved_status"] == "NOT_APPROVED"
    assert by_code["CCTNS"]["last_health_status"] == "DISABLED"
    one = await api.get("/api/v1/integrations/sources/DEMO_SYNTHETIC", headers=as_user(sub))
    assert one.json()["code"] == "DEMO_SYNTHETIC"
    missing = await api.get("/api/v1/integrations/sources/NOPE", headers=as_user(sub))
    assert missing.status_code == 404


async def test_reconciliation_has_no_unexplained_records(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    rows = (await api.get("/api/v1/integrations/reconciliation", headers=as_user(sub))).json()
    assert rows["data"]
    assert all(r["unexplained"] == 0 for r in rows["data"])


async def test_integrations_are_denied_without_lineage_read(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    sub, _ = make_user(Role.SENIOR_LEADERSHIP)
    r = await api.get("/api/v1/integrations/sources", headers=as_user(sub))
    assert r.status_code == 403


# --- Boundary versions and dual-control approval ------------------------------------------------


def _propose_same_geometry(database: Database, unit_code: str) -> str:
    """A proposed version 2 identical to the current one (so other tests' spatial checks hold)."""
    with psycopg.connect(database.migrator, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO core.geographic_boundary (org_unit_id, boundary_type, version, geometry, "
            "source, approval_status) SELECT org_unit_id, boundary_type, version + 1, geometry, "
            "'SYNTHETIC - test proposal', 'PROPOSED' FROM core.geographic_boundary b "
            "JOIN core.organizational_unit o ON o.id = b.org_unit_id "
            "WHERE o.code = %s AND b.valid_to IS NULL AND b.approval_status <> 'PROPOSED' "
            "RETURNING id::text",
            [unit_code],
        ).fetchone()
    assert row is not None
    return str(row[0])


async def test_boundary_needs_two_different_approvers_and_then_supersedes(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    boundary_id = _propose_same_geometry(ingested.database, "YAN-S02-B1")
    admin, _ = make_user(Role.SYSTEM_ADMIN)
    steward, _ = make_user(Role.DATA_STEWARD, ("YAN",))
    url = f"/api/v1/boundaries/{boundary_id}/approvals"

    r = await api.post(url, json={"as_role": "SYSTEM_ADMIN"}, headers=as_user(admin))
    assert r.status_code == 200
    assert r.json()["approval_status"] == "PROPOSED"
    again = await api.post(url, json={"as_role": "SYSTEM_ADMIN"}, headers=as_user(admin))
    assert again.status_code == 409

    headers = {**as_user(steward), "X-Request-ID": BOUNDARY_APPROVE_2}
    r = await api.post(url, json={"as_role": "DATA_STEWARD"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["approval_status"] == "APPROVED"
    assert [x["action"] for x in audit_rows(BOUNDARY_APPROVE_2)] == ["BOUNDARY_APPROVAL"]

    versions = (
        await api.get("/api/v1/boundaries/YAN-S02-B1/versions", headers=as_user(steward))
    ).json()["data"]
    assert [(v["version"], v["approval_status"]) for v in versions] == [
        (2, "APPROVED"),
        (1, "RETIRED"),
    ]
    assert versions[1]["valid_to"] is not None


async def test_one_person_cannot_fill_both_approvals(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    boundary_id = _propose_same_geometry(ingested.database, "YAN-S01-B1")
    sub, user_id = make_user(Role.SYSTEM_ADMIN)
    add_grant(ingested.database, user_id, Role.DATA_STEWARD, ("YAN",))
    url = f"/api/v1/boundaries/{boundary_id}/approvals"
    assert (
        await api.post(url, json={"as_role": "SYSTEM_ADMIN"}, headers=as_user(sub))
    ).status_code == 200
    r = await api.post(url, json={"as_role": "DATA_STEWARD"}, headers=as_user(sub))
    assert r.status_code == 409
    assert r.json()["type"].endswith("separation-of-duties")


async def test_approval_requires_the_matching_role_territory_and_a_fresh_login(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    boundary_id = _propose_same_geometry(ingested.database, "MAH-S01-B1")
    url = f"/api/v1/boundaries/{boundary_id}/approvals"
    other_region, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    r = await api.post(url, json={"as_role": "DATA_STEWARD"}, headers=as_user(other_region))
    assert r.status_code == 403
    analyst, _ = make_user(Role.CRIME_ANALYST, ("MAH",))
    r = await api.post(url, json={"as_role": "DATA_STEWARD"}, headers=as_user(analyst))
    assert r.status_code == 403
    from datetime import timedelta

    stale, _ = make_user(Role.SYSTEM_ADMIN)
    r = await api.post(
        url,
        json={"as_role": "SYSTEM_ADMIN"},
        headers=as_user(stale, auth_age=timedelta(minutes=30)),
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("reauthentication-required")


# --- /auth/me exposes effective permissions for UI navigation --------------------------------


async def test_me_lists_effective_permissions(api: httpx.AsyncClient, make_user: MakeUser) -> None:
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    body = (await api.get("/api/v1/auth/me", headers=as_user(sub))).json()
    assert "dq.manage" in body["permissions"]
    assert "admin.users" not in body["permissions"]
    assert body["mfa_blocked_permissions"] == []
    no_mfa = (await api.get("/api/v1/auth/me", headers=as_user(sub, mfa=False))).json()
    assert no_mfa["permissions"] == []
    assert "dq.manage" in no_mfa["mfa_blocked_permissions"]
    viewer, _ = make_user(Role.DEMO_USER)
    demo = (await api.get("/api/v1/auth/me", headers=as_user(viewer, mfa=False))).json()
    assert demo["mfa_blocked_permissions"] == []  # demo role is MFA-exempt: nothing is blocked


async def test_data_freshness_is_available_to_every_signed_in_user(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sub, _ = make_user(Role.DEMO_USER)
    body = (await api.get("/api/v1/data-freshness", headers=as_user(sub))).json()
    assert body["data_label"] == "DEMO / SYNTHETIC DATA"
    assert body["last_refreshed_at"] is not None
    assert {s["code"] for s in body["sources"]} == {"DEMO_SYNTHETIC", "DEMO_SYNTHETIC_LEGACY"}
    assert set(body["sources"][0]) == {"code", "name", "last_batch_at", "status"}


# --- PRODUCTION refuses to start while demo data is present (demo-data policy 5) -----------------


async def test_production_refuses_to_start_with_demo_data_present(ingested: Ingested) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from crimemap_api.startup import assert_no_demo_data

    engine = create_async_engine(ingested.database.app_async)
    try:
        await assert_no_demo_data(engine, "DEMO")  # demo mode: allowed
        with pytest.raises(RuntimeError, match="demo data"):
            await assert_no_demo_data(engine, "PRODUCTION")
    finally:
        await engine.dispose()


@pytest.mark.parametrize(("environment", "expected"), [("DEMO", True), ("PRODUCTION", False)])
async def test_provisioned_users_are_flagged_demo_only_outside_production(
    database: Database, environment: str, expected: bool
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from crimemap_api.identity import store
    from crimemap_api.identity.tokens import TokenClaims

    engine = create_async_engine(database.app_async)
    claims = TokenClaims(
        subject=str(uuid.uuid4()),
        mfa=True,
        auth_time=None,
        session_id=None,
        username="new-user",
        name=None,
        email=None,
        amr=("pwd", "otp"),
    )
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            user = await store.provision_user(session, claims, environment=environment)
        assert user.is_demo is expected
    finally:
        await engine.dispose()
