"""Phase 2 data foundation: schema, grants, row-level security, immutable raw staging."""

import uuid

import psycopg
import pytest

from .conftest import Database

pytestmark = pytest.mark.integration

PHASE2_TABLES = {
    ("core", "geographic_boundary"),
    ("core", "analytical_category"),
    ("core", "offence_category_map"),
    ("core", "status_definition"),
    ("core", "source_system"),
    ("core", "incident_record"),
    ("core", "incident_location"),
    ("core", "source_record_link"),
    ("core", "data_quality_issue"),
    ("core", "feature_flag"),
    ("core", "denominator"),
    ("staging", "ingest_batch"),
    ("staging", "raw_ingest"),
    ("staging", "dead_letter"),
    ("restricted", "sensitive_source_detail"),
}


def test_phase2_tables_exist(su: psycopg.Connection) -> None:
    rows = su.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema IN ('core','staging','restricted')"
    ).fetchall()
    assert set(rows) >= PHASE2_TABLES


def test_eleven_analytical_categories_are_seeded(app_conn: psycopg.Connection) -> None:
    rows = app_conn.execute(
        "SELECT code, is_sensitive FROM core.analytical_category ORDER BY code"
    ).fetchall()
    codes = {r[0] for r in rows}
    assert len(codes) == 11
    assert {"CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN"} <= {r[0] for r in rows if r[1]}


def test_demo_source_is_approved_and_government_sources_are_stubs(
    app_conn: psycopg.Connection,
) -> None:
    rows = dict(app_conn.execute("SELECT code, approved_status FROM core.source_system").fetchall())
    assert rows["DEMO_SYNTHETIC"] == "DEMO"
    for stub in ("CCTNS", "ERSS_112", "ICJS", "EDAR", "ECHALLAN", "CCTV_ICCC"):
        assert rows[stub] == "NOT_APPROVED"


def test_civic_context_flag_is_off_by_default(app_conn: psycopg.Connection) -> None:
    row = app_conn.execute(
        "SELECT enabled FROM core.feature_flag WHERE key = 'civic_context'"
    ).fetchone()
    assert row == (False,)


def test_app_role_cannot_read_staging_or_restricted(app_conn: psycopg.Connection) -> None:
    for table in (
        "staging.raw_ingest",
        "staging.dead_letter",
        "restricted.sensitive_source_detail",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(f"SELECT 1 FROM {table} LIMIT 1")


def test_app_role_cannot_write_incidents(app_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM core.incident_record")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("UPDATE core.incident_record SET status_code = 'CLOSED'")


@pytest.mark.parametrize(
    "table", ["core.incident_record", "core.incident_location", "core.data_quality_issue"]
)
def test_case_tables_force_row_level_security(su: psycopg.Connection, table: str) -> None:
    schema, name = table.split(".")
    row = su.execute(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s",
        [schema, name],
    ).fetchone()
    assert row == (True, True)


def _insert_incident(database: Database, region_code: str) -> str:
    """Insert one synthetic incident as the worker role (the only writer)."""
    ref = f"TEST-{uuid.uuid4()}"
    with psycopg.connect(database.worker, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO core.incident_record (source_system_id, source_record_reference, "
            "source_version, record_type, reported_at, region_id, sensitivity_classification, "
            "access_classification, is_demo) "
            "SELECT s.id, %s, 1, 'CASE', now(), o.id, 'RESTRICTED', 'RESTRICTED', true "
            "FROM core.source_system s, core.organizational_unit o "
            "WHERE s.code = 'DEMO_SYNTHETIC' AND o.code = %s RETURNING id::text",
            [ref, region_code],
        ).fetchone()
    assert row is not None
    return row[0]


def _visible(
    database: Database, incident_id: str, *, regions: str = "", stations: str = ""
) -> bool:
    with psycopg.connect(database.app) as conn, conn.transaction():
        conn.execute(
            "SELECT set_config('app.region_ids', %s, true), set_config('app.station_ids', %s, true)",
            [regions, stations],
        )
        return (
            conn.execute(
                "SELECT 1 FROM core.incident_record WHERE id = %s", [incident_id]
            ).fetchone()
            is not None
        )


def _region_id(database: Database, code: str) -> str:
    with psycopg.connect(database.app) as conn:
        row = conn.execute(
            "SELECT id::text FROM core.organizational_unit WHERE code = %s", [code]
        ).fetchone()
    assert row is not None
    return row[0]


def test_rls_hides_incidents_without_scope(database: Database) -> None:
    incident = _insert_incident(database, "KKL")
    assert _visible(database, incident) is False


def test_rls_shows_incidents_in_scope_only(database: Database) -> None:
    incident = _insert_incident(database, "KKL")
    assert _visible(database, incident, regions=_region_id(database, "KKL")) is True
    assert _visible(database, incident, regions=_region_id(database, "PDY")) is False


def test_rls_never_shows_highly_restricted_records(database: Database) -> None:
    incident = _insert_incident(database, "MAH")
    with psycopg.connect(database.worker, autocommit=True) as conn:
        conn.execute(
            "UPDATE core.incident_record SET access_classification = 'HIGHLY_RESTRICTED' WHERE id = %s",
            [incident],
        )
    assert _visible(database, incident, regions=_region_id(database, "MAH")) is False


def test_raw_ingest_payload_is_immutable(database: Database) -> None:
    with psycopg.connect(database.worker, autocommit=True) as conn:
        batch = conn.execute(
            "INSERT INTO staging.ingest_batch (source_system_id, batch_ref, record_count, signature_valid) "
            "SELECT id, %s, 1, true FROM core.source_system WHERE code = 'DEMO_SYNTHETIC' RETURNING id",
            [f"test-{uuid.uuid4()}"],
        ).fetchone()
        assert batch is not None
        raw = conn.execute(
            "INSERT INTO staging.raw_ingest (batch_id, source_system_id, payload, content_hash, idempotency_key) "
            "SELECT %s, source_system_id, '{\"a\": 1}', 'h', %s FROM staging.ingest_batch WHERE id = %s "
            "RETURNING id",
            [batch[0], f"key-{uuid.uuid4()}", batch[0]],
        ).fetchone()
        assert raw is not None
        conn.execute("UPDATE staging.raw_ingest SET status = 'VALID' WHERE id = %s", [raw[0]])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE staging.raw_ingest SET payload = '{\"a\": 2}' WHERE id = %s", [raw[0]]
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM staging.raw_ingest WHERE id = %s", [raw[0]])
