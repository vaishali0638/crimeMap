"""Regression tests for the Phase 2 audit findings (docs/phase-2-audit.md). Each test names the
finding it covers and fails on the pre-audit code.

Records here belong to a dedicated feed, TEST_FEED, registered by this module, so the shared session
dataset other modules count against is not disturbed.
"""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from crimemap_worker import ingest as pipeline
from crimemap_worker.ingest import IngestResult, ingest_batch_file, ingest_directory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.ingest.signing import FORMAT, sign
from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import KEY, Database, Ingested, run

pytestmark = pytest.mark.integration

FEED = "TEST_FEED"
MakeUser = Callable[..., tuple[str, str]]


# --- helpers -------------------------------------------------------------------------------------


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator, autocommit=True) as conn:
        cur = conn.execute(query, list(params))
        return cur.fetchall() if cur.description else []


def one(database: Database, query: str, *params: object) -> Any:
    return sql(database, query, *params)[0][0]


@pytest.fixture(scope="module")
def feed(ingested: Ingested) -> Database:
    sql(
        ingested.database,
        "INSERT INTO core.source_system (code, name, classification, approved_status, adapter_type, "
        "owner, sync_policy, last_health_status) VALUES (%s, 'Audit test feed', 'RESTRICTED', 'DEMO', "
        "'signed-json-batch', 'tests', '{\"stale_after_hours\": 48, \"mapping_version\": \"DEMO-2026.1\"}', "
        "'NEVER_RUN') ON CONFLICT (code) DO NOTHING",
        FEED,
    )
    return ingested.database


def interior(database: Database, unit_code: str) -> tuple[float, float]:
    lon, lat = sql(
        database,
        "SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_PointOnSurface(geometry) p FROM core.current_boundary "
        "WHERE code = %s) t",
        unit_code,
    )[0]
    return float(lon), float(lat)


def record(
    ref: str, *, at: tuple[float, float] | None, station: str, **overrides: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_system": FEED,
        "source_record_reference": ref,
        "source_version": 1,
        "record_type": "CASE",
        "official_offence_act": "Synthetic Demo Code",
        "official_offence_section": "D-101",
        "official_offence_name": "demo",
        "event_type_code": None,
        "occurrence_start_at": "2026-09-01T10:00:00+05:30",
        "occurrence_end_at": "2026-09-01T10:30:00+05:30",
        "reported_at": "2026-09-01T12:00:00+05:30",
        "reporting_station_code": station,
        "status": "OPEN",
        "location": None
        if at is None
        else {
            "lat": at[1],
            "lon": at[0],
            "text": "demo text",
            "locality": "demo locality",
            "precision": "EXACT",
            "method": "SOURCE_COORDINATE",
            "confidence": 0.95,
            "is_residential": False,
        },
        "sensitivity_classification": "RESTRICTED",
        "restricted": None,
    }
    base.update(overrides)
    return base


def batch_file(
    tmp: Path,
    records: list[dict[str, Any]],
    *,
    ref: str | None = None,
    source: str = FEED,
    extracted_at: datetime | None = None,
) -> Path:
    envelope = {
        "format": FORMAT,
        "source_system": source,
        "batch_ref": ref or f"audit-{uuid.uuid4()}",
        "extracted_at": (extracted_at or datetime.now(UTC)).isoformat(),
        "record_count": len(records),
        "records": records,
    }
    path = tmp / f"{envelope['batch_ref']}.json"
    path.write_text(json.dumps({"envelope": envelope, "signature": sign(envelope, KEY)}))
    return path


async def aingest(database: Database, path: Path, **kw: Any) -> IngestResult:
    engine = create_async_engine(database.worker_async)
    try:
        return await ingest_batch_file(engine, path, KEY, environment="DEMO", **kw)
    finally:
        await engine.dispose()


def ingest(database: Database, path: Path, **kw: Any) -> IngestResult:
    return run(aingest(database, path, **kw))


def ref() -> str:
    return f"AUDIT-{uuid.uuid4().hex[:12]}"


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


# --- H1: one lax-typed record no longer aborts the batch ------------------------------------------


@pytest.mark.parametrize("bad_version", ["2", 2.0, 2**40])
def test_a_lax_typed_record_is_dead_lettered_and_the_batch_still_loads(
    feed: Database, tmp_path: Path, bad_version: object
) -> None:
    at = interior(feed, "PDY-S01-B1")
    good, bad = ref(), ref()
    result = ingest(
        feed,
        batch_file(
            tmp_path,
            [
                record(good, at=at, station="PDY-S01"),
                record(bad, at=at, station="PDY-S01", source_version=bad_version),
            ],
        ),
    )
    assert result.status == "PROCESSED"
    assert (result.accepted, result.rejected) == (1, 1)
    assert (
        one(
            feed,
            "SELECT count(*) FROM core.incident_record WHERE source_record_reference = %s",
            good,
        )
        == 1
    )
    codes = sql(
        feed,
        "SELECT d.error_code FROM staging.dead_letter d JOIN staging.raw_ingest r ON r.id = d.raw_ingest_id "
        "WHERE r.payload->>'source_record_reference' = %s",
        bad,
    )
    assert codes == [("SCHEMA_INVALID",)]


# --- H2: a failing batch is recorded, audited, does not stop the run, and can be re-delivered ----


def test_a_failing_batch_is_recorded_and_the_rest_of_the_directory_still_loads(
    feed: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = interior(feed, "PDY-S02-B1")
    first = batch_file(tmp_path, [record(ref(), at=at, station="PDY-S02")], ref="audit-h2-a")
    batch_file(tmp_path, [record(ref(), at=at, station="PDY-S02")], ref="audit-h2-b")
    calls = {"n": 0}

    async def failing_once(conn: Any, changed: list[Any]) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            await conn.execute(text("SELECT 1 / 0"))
        return await real_run_rules(conn, changed)

    real_run_rules = pipeline._run_rules
    monkeypatch.setattr(pipeline, "_run_rules", failing_once)

    async def _go(directory: Path) -> list[IngestResult]:
        engine = create_async_engine(feed.worker_async)
        try:
            return await ingest_directory(engine, directory, KEY, environment="DEMO")
        finally:
            await engine.dispose()

    results = run(_go(tmp_path))
    assert [r.status for r in results] == ["FAILED", "PROCESSED"]
    assert (
        one(feed, "SELECT status FROM staging.ingest_batch WHERE batch_ref = 'audit-h2-a'")
        == "FAILED"
    )
    assert (
        one(
            feed,
            "SELECT count(*) FROM staging.raw_ingest r JOIN staging.ingest_batch b ON b.id = r.batch_id "
            "WHERE b.batch_ref = 'audit-h2-a'",
        )
        == 0
    )
    assert (
        one(
            feed,
            "SELECT count(*) FROM audit.audit_event WHERE action = 'INGEST_FAILED' "
            "AND after_meta->>'batch_ref' = 'audit-h2-a'",
        )
        == 1
    )
    # Re-delivery after the fault is fixed processes the failed batch.
    again = ingest(feed, first)
    assert again.status == "PROCESSED"
    assert (
        one(feed, "SELECT status FROM staging.ingest_batch WHERE batch_ref = 'audit-h2-a'")
        == "PROCESSED"
    )


# --- H3: exact residential locations never reach the analytical store -----------------------------


def test_residential_locations_are_generalised_and_the_exact_point_is_restricted(
    feed: Database, tmp_path: Path
) -> None:
    at = interior(feed, "PDY-S03-B1")
    r = ref()
    rec = record(r, at=at, station="PDY-S03")
    rec["location"]["is_residential"] = True
    assert ingest(feed, batch_file(tmp_path, [rec])).status == "PROCESSED"
    precision, text_value, moved, covered = sql(
        feed,
        "SELECT l.precision_level, l.location_text, NOT ST_Equals(l.geometry, d.residential_location), "
        "ST_Covers(b.geometry, l.geometry) FROM core.incident_record i "
        "JOIN core.incident_location l ON l.incident_id = i.id "
        "JOIN restricted.sensitive_source_detail d ON d.incident_id = i.id "
        "JOIN core.current_boundary b ON b.org_unit_id = i.beat_id WHERE i.source_record_reference = %s",
        r,
    )[0]
    assert precision == "GRID"
    assert text_value is None
    assert moved is True
    assert covered is True
    exact = one(
        feed,
        "SELECT ST_AsText(residential_location) FROM restricted.sensitive_source_detail d "
        "JOIN core.incident_record i ON i.id = d.incident_id WHERE i.source_record_reference = %s",
        r,
    )
    assert exact == f"POINT({at[0]} {at[1]})"


def test_no_exact_residential_point_remains_anywhere_in_core(feed: Database) -> None:
    assert (
        one(
            feed,
            "SELECT count(*) FROM core.incident_location WHERE is_residential "
            "AND precision_level NOT IN ('GRID', 'UNKNOWN')",
        )
        == 0
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(
            feed,
            "UPDATE core.incident_location SET precision_level = 'EXACT' WHERE incident_id = "
            "(SELECT incident_id FROM core.incident_location WHERE is_residential LIMIT 1)",
        )


# --- H4: lineage links follow incident visibility --------------------------------------------------


def test_lineage_links_are_hidden_from_the_api_role_without_scope(feed: Database) -> None:
    with psycopg.connect(feed.app) as conn:
        assert conn.execute("SELECT count(*) FROM core.source_record_link").fetchone() == (0,)
        conn.execute(
            "SELECT set_config('app.region_ids', (SELECT id::text FROM core.organizational_unit "
            "WHERE code = 'KKL'), true)"
        )
        regions = conn.execute(
            "SELECT DISTINCT o.code FROM core.source_record_link k JOIN core.incident_record i "
            "ON i.id = k.incident_id JOIN core.organizational_unit o ON o.id = i.region_id"
        ).fetchall()
    assert regions == [("KKL",)]


# --- H5: the approval function takes the approver from the session and checks the role ----------


def test_approval_function_cannot_be_called_for_someone_else(feed: Database) -> None:
    boundary = one(
        feed,
        "INSERT INTO core.geographic_boundary (org_unit_id, boundary_type, version, geometry, source, "
        "approval_status) SELECT org_unit_id, unit_type, 99, geometry, 'SYNTHETIC - audit', 'PROPOSED' "
        "FROM core.current_boundary WHERE code = 'KKL-S03-B1' RETURNING id::text",
    )
    nobody = one(
        feed,
        "INSERT INTO core.app_user (idp_subject, username) VALUES (%s, 'no-grants') RETURNING id::text",
        str(uuid.uuid4()),
    )
    with psycopg.connect(feed.app) as conn:
        with pytest.raises(psycopg.Error) as no_user:
            conn.execute("SELECT core.approve_boundary(%s, 'ADMIN')", [boundary])  # no session user
        assert no_user.value.sqlstate == "CM002"
        conn.rollback()
        conn.execute("SELECT set_config('app.user_id', %s, false)", [nobody])
        with pytest.raises(psycopg.Error) as no_role:
            conn.execute("SELECT core.approve_boundary(%s, 'ADMIN')", [boundary])  # holds no role
        assert no_role.value.sqlstate == "CM002"
        conn.rollback()
        with pytest.raises(psycopg.errors.UndefinedFunction):
            conn.execute("SELECT core.approve_boundary(%s, %s, 'ADMIN')", [boundary, nobody])
    assert (
        one(feed, "SELECT approved_admin_by FROM core.geographic_boundary WHERE id = %s", boundary)
        is None
    )


# --- H6: corrections and version conflicts ---------------------------------------------------------


def test_a_corrected_record_replaces_its_dead_letter(feed: Database, tmp_path: Path) -> None:
    at = interior(feed, "KKL-S01-B1")
    r = ref()
    broken = record(r, at=at, station="KKL-S01", reported_at=None)
    assert ingest(feed, batch_file(tmp_path, [broken])).rejected == 1
    fixed = record(r, at=at, station="KKL-S01")
    result = ingest(feed, batch_file(tmp_path, [fixed]))
    assert (result.accepted, result.duplicates) == (1, 0)
    assert (
        one(feed, "SELECT count(*) FROM core.incident_record WHERE source_record_reference = %s", r)
        == 1
    )
    assert (
        one(
            feed,
            "SELECT bool_and(d.resolved_at IS NOT NULL) FROM staging.dead_letter d "
            "JOIN staging.raw_ingest x ON x.id = d.raw_ingest_id WHERE x.payload->>'source_record_reference' = %s",
            r,
        )
        is True
    )


def test_different_content_under_an_accepted_version_is_a_conflict(
    feed: Database, tmp_path: Path
) -> None:
    at = interior(feed, "KKL-S01-B2")
    r = ref()
    assert ingest(feed, batch_file(tmp_path, [record(r, at=at, station="KKL-S01")])).accepted == 1
    changed = record(r, at=at, station="KKL-S01", status="CLOSED")
    result = ingest(feed, batch_file(tmp_path, [changed]))
    assert (result.accepted, result.rejected, result.duplicates) == (0, 1, 0)
    assert (
        one(
            feed,
            "SELECT status_code FROM core.incident_record WHERE source_record_reference = %s",
            r,
        )
        == "OPEN"
    )
    assert (
        one(
            feed,
            "SELECT d.error_code FROM staging.dead_letter d JOIN staging.raw_ingest x ON x.id = d.raw_ingest_id "
            "WHERE x.payload->>'source_record_reference' = %s",
            r,
        )
        == "VERSION_CONFLICT"
    )


# --- H7: a re-used batch reference with different content is rejected ----------------------------


def test_a_reused_batch_reference_with_new_content_is_rejected(
    feed: Database, tmp_path: Path
) -> None:
    at = interior(feed, "KKL-S02-B1")
    (tmp_path / "a").mkdir()
    first = batch_file(tmp_path / "a", [record(ref(), at=at, station="KKL-S02")], ref="audit-h7")
    assert ingest(feed, first).status == "PROCESSED"
    (tmp_path / "b").mkdir()
    other = batch_file(tmp_path / "b", [record(ref(), at=at, station="KKL-S02")], ref="audit-h7")
    raw_before = one(feed, "SELECT count(*) FROM staging.raw_ingest")
    assert ingest(feed, other).status == "REJECTED_BATCH_REF"
    assert one(feed, "SELECT count(*) FROM staging.raw_ingest") == raw_before
    assert (
        one(feed, "SELECT count(*) FROM audit.audit_event WHERE reason = 'BATCH_REF_CONFLICT'") >= 1
    )
    assert ingest(feed, first).status == "ALREADY_PROCESSED"


# --- M1: issues follow their incident to its new territory ---------------------------------------


def test_issues_move_with_an_incident_that_changes_region(feed: Database, tmp_path: Path) -> None:
    r = ref()
    v1 = record(
        r,
        at=interior(feed, "PDY-S04-B1"),
        station="PDY-S04",
        occurrence_start_at=None,
        occurrence_end_at=None,
    )
    ingest(feed, batch_file(tmp_path, [v1]))
    v2 = record(
        r,
        at=interior(feed, "KKL-S03-B2"),
        station="KKL-S03",
        occurrence_start_at=None,
        occurrence_end_at=None,
        source_version=2,
    )
    ingest(feed, batch_file(tmp_path, [v2]))
    regions = sql(
        feed,
        "SELECT DISTINCT o.code FROM core.data_quality_issue q JOIN core.incident_record i ON i.id = q.incident_id "
        "JOIN core.organizational_unit o ON o.id = q.region_id WHERE i.source_record_reference = %s",
        r,
    )
    assert regions == [("KKL",)]
    location_region = sql(
        feed,
        "SELECT o.code FROM core.incident_location l JOIN core.incident_record i ON i.id = l.incident_id "
        "JOIN core.organizational_unit o ON o.id = l.region_id WHERE i.source_record_reference = %s",
        r,
    )
    assert location_region == [("KKL",)]  # the location's RLS scope copy followed the move


# --- M2: a duplicate pair is found whichever record arrives first ---------------------------------


def test_duplicates_are_flagged_when_the_earlier_report_arrives_later(
    feed: Database, tmp_path: Path
) -> None:
    at = interior(feed, "YAN-S01-B1")
    earlier, later = ref(), ref()
    ingest(
        feed,
        batch_file(
            tmp_path,
            [record(later, at=at, station="YAN-S01", reported_at="2026-09-01T13:00:00+05:30")],
        ),
    )
    ingest(
        feed,
        batch_file(
            tmp_path,
            [record(earlier, at=at, station="YAN-S01", reported_at="2026-09-01T12:00:00+05:30")],
        ),
    )
    flagged = sql(
        feed,
        "SELECT i.source_record_reference, q.detail->>'duplicate_of_id' = e.id::text FROM core.data_quality_issue q "
        "JOIN core.incident_record i ON i.id = q.incident_id "
        "JOIN core.incident_record e ON e.source_record_reference = %s "
        "WHERE q.rule_code = 'DQ-DUP-001' AND i.source_record_reference IN (%s, %s)",
        earlier,
        earlier,
        later,
    )
    assert flagged == [(later, True)]


# --- M3: the demo key can never load an approved source -------------------------------------------


def test_an_approved_source_is_refused_with_the_demo_key(feed: Database, tmp_path: Path) -> None:
    sql(feed, "UPDATE core.source_system SET approved_status = 'APPROVED' WHERE code = %s", FEED)
    try:
        path = batch_file(tmp_path, [record(ref(), at=None, station="PDY-S01")])
        with pytest.raises(PermissionError, match="no signing key"):
            ingest(feed, path)
    finally:
        sql(feed, "UPDATE core.source_system SET approved_status = 'DEMO' WHERE code = %s", FEED)


# --- M5: freshness cannot be faked; rejected batches are counted ----------------------------------


def test_a_future_extraction_time_is_clamped(feed: Database, tmp_path: Path) -> None:
    path = batch_file(
        tmp_path,
        [record(ref(), at=None, station="PDY-S01")],
        ref="audit-future",
        extracted_at=datetime(2099, 1, 1, tzinfo=UTC),
    )
    ingest(feed, path)
    assert (
        one(
            feed,
            "SELECT source_extracted_at <= received_at + interval '5 minutes' FROM staging.ingest_batch "
            "WHERE batch_ref = 'audit-future'",
        )
        is True
    )


def test_a_rejected_signature_is_counted_against_the_claimed_source(
    feed: Database, tmp_path: Path
) -> None:
    path = batch_file(tmp_path, [record(ref(), at=None, station="PDY-S01")])
    raw = json.loads(path.read_text())
    raw["envelope"]["records"][0]["status"] = "CLOSED"
    path.write_text(json.dumps(raw))
    assert ingest(feed, path).status == "REJECTED_SIGNATURE"
    assert (
        one(
            feed,
            "SELECT sum(rejected_batches) FROM core.ingest_reconciliation WHERE source_code = %s",
            FEED,
        )
        >= 1
    )


def test_a_batch_from_the_wrong_adapter_is_rejected(feed: Database, tmp_path: Path) -> None:
    path = batch_file(tmp_path, [record(ref(), at=None, station="PDY-S01")])
    assert ingest(feed, path, expected_source="DEMO_SYNTHETIC").status == "REJECTED_SOURCE"


# --- M6: quality status follows manual review -----------------------------------------------------


def test_quality_status_is_refreshed_when_a_reviewer_closes_the_issues(
    feed: Database, tmp_path: Path
) -> None:
    r = ref()
    ingest(
        feed, batch_file(tmp_path, [record(r, at=None, station="MAH-S01")])
    )  # missing location: HIGH
    assert (
        one(
            feed,
            "SELECT record_quality_status FROM core.incident_record WHERE source_record_reference = %s",
            r,
        )
        == "FAILED"
    )
    sql(
        feed,
        "UPDATE core.data_quality_issue SET state = 'ACCEPTED_AS_IS', resolution_note = 'checked at source' "
        "WHERE incident_id = (SELECT id FROM core.incident_record WHERE source_record_reference = %s)",
        r,
    )
    assert (
        one(
            feed,
            "SELECT record_quality_status FROM core.incident_record WHERE source_record_reference = %s",
            r,
        )
        == "OK"
    )


# --- API: F1-F8 ------------------------------------------------------------------------------------


async def _hr_pair(feed: Database, tmp_path: Path, beat: str) -> tuple[str, str]:
    """A HIGHLY_RESTRICTED record and a later near-duplicate of it in the same station."""
    at = interior(feed, beat)
    hidden, visible = ref(), ref()
    secret = record(
        hidden,
        at=at,
        station="MAH-S02",
        sensitivity_classification="HIGHLY_RESTRICTED",
        reported_at="2026-09-01T12:00:00+05:30",
    )
    secret["location"]["confidence"] = 0.2  # raises its own LOW_CONFIDENCE_LOCATION issue
    await aingest(
        feed,
        batch_file(
            tmp_path,
            [
                secret,
                record(
                    visible,
                    at=at,
                    station="MAH-S02",
                    reported_at="2026-09-01T12:10:00+05:30",
                    occurrence_start_at="2026-09-01T10:00:00+05:30",
                ),
            ],
        ),
    )
    return hidden, visible


async def test_issues_on_hidden_records_are_not_listed_or_changeable(
    feed: Database, tmp_path: Path, api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """F3: a steward could see and permanently accept issues on records it cannot see."""
    hidden, _ = await _hr_pair(feed, tmp_path, "MAH-S02-B1")
    issue_id = one(
        feed,
        "SELECT q.id::text FROM core.data_quality_issue q JOIN core.incident_record i ON i.id = q.incident_id "
        "WHERE i.source_record_reference = %s LIMIT 1",
        hidden,
    )
    sub, _ = make_user(Role.DATA_STEWARD, ("MAH",))
    ids: list[str] = []
    cursor = None
    while True:
        q = "/api/v1/dq/issues?limit=100" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(q, headers=as_user(sub))).json()
        ids += [i["id"] for i in page["data"]]
        cursor = page["pagination"]["next_cursor"]
        if not page["pagination"]["has_more"]:
            break
    assert issue_id not in ids
    r = await api.patch(
        f"/api/v1/dq/issues/{issue_id}",
        json={"state": "ACCEPTED_AS_IS", "resolution_note": "cannot see it"},
        headers=as_user(sub),
    )
    assert r.status_code == 404


async def test_a_duplicate_partner_that_is_hidden_is_not_named(
    feed: Database, tmp_path: Path, api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """F2: the duplicate detail named the partner's case reference, even a HIGHLY_RESTRICTED one."""
    hidden, visible = await _hr_pair(feed, tmp_path, "MAH-S02-B2")
    sub, _ = make_user(Role.DATA_STEWARD, ("MAH",))
    page = (
        await api.get(
            "/api/v1/dq/issues?issue_type=POSSIBLE_DUPLICATE&limit=100", headers=as_user(sub)
        )
    ).json()
    mine = [i for i in page["data"] if i["incident"] and i["incident"]["reference"] == visible]
    assert len(mine) == 1
    assert mine[0]["duplicate_of"] == {"visible": False, "reference": None}
    assert hidden not in json.dumps(page)
    assert "duplicate_of_id" not in mine[0]["detail"]


async def test_a_steward_without_territory_is_denied(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """F4: an empty-scope steward could list and close source-wide issues."""
    sub, _ = make_user(Role.DATA_STEWARD)
    assert (await api.get("/api/v1/dq/issues", headers=as_user(sub))).status_code == 403


async def test_an_out_of_scope_region_filter_is_refused_and_audited(
    feed: Database, api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any
) -> None:
    """F5: the filter used to be narrowed silently to an empty result."""
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get("/api/v1/dq/issues?region=KKL", headers={**as_user(sub), "X-Request-ID": rid})
    assert r.status_code == 403
    assert [(x["action"], x["result"]) for x in audit_rows(rid)] == [("AUTHZ_DENIED", "DENY")]


@pytest.mark.parametrize(
    "payload",
    [
        ["2026-01-01T00:00:00+00:00", 5],
        ["2026-01-01T00:00:00+00:00", ["x"]],
        ["2026-01-01T00:00:00", str(uuid.uuid4())],
        "x",
    ],
)
async def test_malformed_cursors_are_a_400(
    api: httpx.AsyncClient, make_user: MakeUser, payload: object
) -> None:
    """F6: some tampered cursors caused a 500."""
    import base64

    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    r = await api.get(f"/api/v1/dq/issues?cursor={cursor}", headers=as_user(sub))
    assert r.status_code == 400


async def test_boundary_history_is_scoped_to_the_stewards_region(
    feed: Database, api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """F7."""
    sub, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    assert (
        await api.get("/api/v1/boundaries/PDY-S01/versions", headers=as_user(sub))
    ).status_code == 200
    assert (
        await api.get("/api/v1/boundaries/KKL/versions", headers=as_user(sub))
    ).status_code == 403


async def test_unknown_boundary_needs_authorisation_first_and_is_audited(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any
) -> None:
    """F8: any signed-in user could probe boundary ids, unaudited."""
    nobody, _ = make_user()
    url = f"/api/v1/boundaries/{uuid.uuid4()}/approvals"
    assert (
        await api.post(url, json={"as_role": "SYSTEM_ADMIN"}, headers=as_user(nobody))
    ).status_code == 403
    admin, _ = make_user(Role.SYSTEM_ADMIN)
    rid = str(uuid.uuid4())
    r = await api.post(
        url, json={"as_role": "SYSTEM_ADMIN"}, headers={**as_user(admin), "X-Request-ID": rid}
    )
    assert r.status_code == 404
    assert [(x["action"], x["result"]) for x in audit_rows(rid)] == [("BOUNDARY_APPROVAL", "DENY")]


async def test_summary_and_refused_transitions_are_audited(
    feed: Database, tmp_path: Path, api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any
) -> None:
    """F8."""
    sub, _ = make_user(Role.DATA_STEWARD, ("MAH",))
    rid = str(uuid.uuid4())
    await api.get("/api/v1/dq/summary", headers={**as_user(sub), "X-Request-ID": rid})
    assert [x["action"] for x in audit_rows(rid)] == ["RECORD_VIEW"]
    r = ref()
    await aingest(feed, batch_file(tmp_path, [record(r, at=None, station="MAH-S01")]))
    issue_id = one(
        feed,
        "SELECT q.id::text FROM core.data_quality_issue q JOIN core.incident_record i ON i.id = q.incident_id "
        "WHERE i.source_record_reference = %s",
        r,
    )
    rid = str(uuid.uuid4())
    resp = await api.patch(
        f"/api/v1/dq/issues/{issue_id}",
        json={"state": "OPEN"},
        headers={**as_user(sub), "X-Request-ID": rid},
    )
    assert resp.status_code == 409
    rows = audit_rows(rid)
    assert [(x["action"], x["result"], x["reason"]) for x in rows] == [
        ("DQ_ISSUE_UPDATE", "DENY", "INVALID_TRANSITION")
    ]
