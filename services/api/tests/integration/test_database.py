"""Database-level guarantees: least-privilege roles, immutable hash-chained audit log, catalogue."""

import uuid

import psycopg
import pytest
from alembic import command

from crimemap_api.policy.catalogue import PERMISSIONS, ROLE_PERMISSIONS, Role

from .conftest import Database, alembic_config

pytestmark = pytest.mark.integration


def insert_event(conn: psycopg.Connection, action: str = "TEST_EVENT", **cols: object) -> None:
    fields = {"action": action, "result": "ALLOW", "environment": "DEMO", **cols}
    names = ", ".join(fields)
    marks = ", ".join(["%s"] * len(fields))
    conn.execute(f"INSERT INTO audit.audit_event ({names}) VALUES ({marks})", list(fields.values()))


def verify(conn: psycopg.Connection) -> tuple[bool, int, int | None, str | None]:
    row = conn.execute(
        "SELECT ok, checked, first_bad_seq, problem FROM audit.verify_chain()"
    ).fetchone()
    assert row is not None
    return row  # type: ignore[return-value]


# --- Roles and privileges -------------------------------------------------------------------


def test_app_role_is_not_owner_superuser_or_rls_exempt(su: psycopg.Connection) -> None:
    row = su.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'crimemap_app'"
    ).fetchone()
    assert row == (False, False, False, False)
    owners = su.execute(
        "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname IN ('core', 'audit')"
    ).fetchall()
    assert owners == [("crimemap_migrator",)]


def test_app_role_cannot_create_tables(app_conn: psycopg.Connection) -> None:
    for schema in ("core", "audit", "public"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute(f"CREATE TABLE {schema}.evil (id int)")


def test_app_role_cannot_read_chain_head(app_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("SELECT * FROM audit.chain_head")


def test_app_role_cannot_modify_roles_or_permissions(app_conn: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("INSERT INTO core.role (code, name) VALUES ('ROOT', 'Root')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM core.role_permission")


# --- Audit log immutability -------------------------------------------------------------------


def test_app_role_can_append_audit_events(app_conn: psycopg.Connection) -> None:
    rid = str(uuid.uuid4())
    insert_event(app_conn, request_id=rid)
    row = app_conn.execute(
        "SELECT seq > 0, length(row_hash), length(prev_hash) FROM audit.audit_event WHERE request_id = %s",
        [rid],
    ).fetchone()
    assert row == (True, 64, 64)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit.audit_event SET reason = 'covered up'",
        "DELETE FROM audit.audit_event",
        "TRUNCATE audit.audit_event",
    ],
)
def test_app_role_cannot_change_audit_history(app_conn: psycopg.Connection, sql: str) -> None:
    insert_event(app_conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_conn.execute(sql)


@pytest.mark.parametrize("column", ["seq", "occurred_at", "prev_hash", "row_hash"])
def test_app_role_cannot_set_chain_columns(app_conn: psycopg.Connection, column: str) -> None:
    value: object = (
        1 if column == "seq" else ("2000-01-01T00:00:00Z" if column == "occurred_at" else "0" * 64)
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        insert_event(app_conn, **{column: value})


def test_owner_is_blocked_by_immutability_trigger(database: Database) -> None:
    with psycopg.connect(database.migrator, autocommit=True) as owner:
        insert_event(owner)
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            owner.execute("UPDATE audit.audit_event SET reason = 'x'")
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            owner.execute("DELETE FROM audit.audit_event")


# --- Hash chain --------------------------------------------------------------------------------


def test_chain_links_consecutive_events(app_conn: psycopg.Connection) -> None:
    rid = str(uuid.uuid4())
    for _ in range(3):
        insert_event(app_conn, request_id=rid)
    rows = app_conn.execute(
        "SELECT seq, prev_hash, row_hash FROM audit.audit_event WHERE request_id = %s ORDER BY seq",
        [rid],
    ).fetchall()
    assert [r[0] for r in rows] == [rows[0][0], rows[0][0] + 1, rows[0][0] + 2]
    assert rows[1][1] == rows[0][2]
    assert rows[2][1] == rows[1][2]


def test_untampered_chain_verifies(app_conn: psycopg.Connection) -> None:
    insert_event(app_conn, filters='{"region": ["PDY"], "note": null}', ip_address="10.0.0.7")
    ok, checked, bad, problem = verify(app_conn)
    assert (ok, bad, problem) == (True, None, None)
    assert checked >= 1


def test_verification_is_independent_of_session_time_zone(app_conn: psycopg.Connection) -> None:
    insert_event(app_conn)
    app_conn.execute("SET TimeZone = 'Asia/Kolkata'")
    assert verify(app_conn)[0] is True


def _tamper(su: psycopg.Connection, sql: str) -> None:
    su.execute("ALTER TABLE audit.audit_event DISABLE TRIGGER USER")
    try:
        su.execute(sql)
    finally:
        su.execute("ALTER TABLE audit.audit_event ENABLE TRIGGER USER")


def test_edited_event_is_detected(su: psycopg.Connection, app_conn: psycopg.Connection) -> None:
    rid = str(uuid.uuid4())
    insert_event(app_conn, request_id=rid, reason="original")
    insert_event(app_conn)
    seq = su.execute("SELECT seq FROM audit.audit_event WHERE request_id = %s", [rid]).fetchone()[0]  # type: ignore[index]
    _tamper(su, f"UPDATE audit.audit_event SET reason = 'edited' WHERE seq = {seq}")
    try:
        ok, _, bad, problem = verify(app_conn)
        assert (ok, bad, problem) == (False, seq, "hash_mismatch")
    finally:
        _tamper(su, f"UPDATE audit.audit_event SET reason = 'original' WHERE seq = {seq}")
    assert verify(app_conn)[0] is True


def test_deleted_event_is_detected(su: psycopg.Connection, app_conn: psycopg.Connection) -> None:
    rid = str(uuid.uuid4())
    insert_event(app_conn, request_id=rid)
    insert_event(app_conn)
    row = su.execute(
        "SELECT seq, to_jsonb(e) FROM audit.audit_event e WHERE request_id = %s", [rid]
    ).fetchone()
    assert row is not None
    seq, saved = row
    _tamper(su, f"DELETE FROM audit.audit_event WHERE seq = {seq}")
    try:
        ok, _, bad, problem = verify(app_conn)
        assert (ok, bad, problem) == (False, seq, "gap")
    finally:
        su.execute("ALTER TABLE audit.audit_event DISABLE TRIGGER USER")
        su.execute(
            "INSERT INTO audit.audit_event SELECT * FROM jsonb_populate_record(NULL::audit.audit_event, %s)",
            [psycopg.types.json.Jsonb(saved)],
        )
        su.execute("ALTER TABLE audit.audit_event ENABLE TRIGGER USER")
    assert verify(app_conn)[0] is True


# --- Reference data ----------------------------------------------------------------------------


def test_four_regions_are_seeded_with_their_analysis_crs(app_conn: psycopg.Connection) -> None:
    rows = app_conn.execute(
        "SELECT code, analysis_crs FROM core.organizational_unit WHERE unit_type = 'REGION' ORDER BY code"
    ).fetchall()
    assert rows == [("KKL", 32644), ("MAH", 32643), ("PDY", 32644), ("YAN", 32644)]


def test_catalogue_in_database_matches_code(app_conn: psycopg.Connection) -> None:
    roles = {r[0] for r in app_conn.execute("SELECT code FROM core.role").fetchall()}
    perms = {r[0] for r in app_conn.execute("SELECT code FROM core.permission").fetchall()}
    pairs = set(
        app_conn.execute("SELECT role_code, permission_code FROM core.role_permission").fetchall()
    )
    assert roles == {r.value for r in Role}
    assert perms == set(PERMISSIONS)
    assert pairs == {(r.value, p) for r, ps in ROLE_PERMISSIONS.items() for p in ps}


def test_postgis_is_available(app_conn: psycopg.Connection) -> None:
    version = app_conn.execute("SELECT postgis_lib_version()").fetchone()
    assert version is not None
    assert version[0].startswith("3.")


def test_migrations_downgrade_and_upgrade_cleanly(database: Database) -> None:
    """Round trip in a scratch database prepared like 01-roles.sh, so the shared session database
    (and the data other modules seeded into it) is left untouched."""
    scratch = "crimemap_roundtrip"
    with psycopg.connect(database.superuser, autocommit=True) as su:
        su.execute(f'CREATE DATABASE "{scratch}" OWNER crimemap_migrator')
    url = database.migrator.rsplit("/", 1)[0] + f"/{scratch}"
    try:
        with psycopg.connect(
            database.superuser.rsplit("/", 1)[0] + f"/{scratch}", autocommit=True
        ) as su:
            su.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            su.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            su.execute("GRANT USAGE ON SCHEMA public TO crimemap_app, crimemap_worker")
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        from crimemap_api.cli import sync_catalogue

        sync_catalogue(url)
    finally:
        alembic_config(database.migrator)  # restore the env var other tests rely on
        with psycopg.connect(database.superuser, autocommit=True) as su:
            su.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
