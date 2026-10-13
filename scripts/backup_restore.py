#!/usr/bin/env python3
"""Encrypted database backup and restore test (Phase 8). DEMO / SYNTHETIC DATA.

    services/api/.venv/bin/python scripts/backup_restore.py backup [--out backups]
    services/api/.venv/bin/python scripts/backup_restore.py restore-test backups/<file>.dump.gpg [--keep]

backup
    One consistent snapshot: the manifest (row counts of every table, migration version, audit
    chain check, row-level security and policy counts) is read in the same exported snapshot that
    ``pg_dump`` uses. The dump (custom format) is encrypted with GnuPG (AES-256, integrity-protected)
    inside the database container, so no plaintext dump touches the host disk. The manifest holds
    counts only, and the SHA-256 of the encrypted file.

restore-test
    Checks the file's SHA-256, decrypts and restores it into a scratch database on the same server
    (never the live one), then compares it with the manifest: every table count, the migration
    version, the audit hash chain, row-level security and policies; it also checks that the
    application role sees no case data without a scope and all of it with one. The scratch database
    is dropped afterwards unless ``--keep``. Writes a JSON report (counts and results only).

Secrets come from the environment (.env): ``POSTGRES_SUPERUSER_PASSWORD``,
``CRIMEMAP_APP_PASSWORD`` and ``CRIMEMAP_BACKUP_PASSPHRASE``. They are passed to ``docker exec`` by
name (``-e NAME``), never on a command line, and never printed.

Local/demo tool. Production uses the platform's managed, encrypted backups with WAL archiving
(point-in-time recovery); see docs/runbook.md §4.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
SCHEMAS = ("core", "audit", "restricted", "staging")
SCRATCH = re.compile(r"^[a-z_][a-z0-9_]{0,40}_restore_test$")

# Fixed scripts run inside the database container. Parameters arrive as environment variables, so
# nothing is interpolated into a shell.
DUMP = (
    # pipefail: a failed pg_dump must fail the backup, not hand gpg a truncated stream (Phase 8 audit).
    "set -o pipefail; "
    'pg_dump -U postgres -h 127.0.0.1 -d "$DB" -Fc --snapshot="$SNAPSHOT" '
    "| gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 "
    "--s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712 --compress-algo none "
    "--passphrase-fd 3 -o - 3<<EOF\n$CRIMEMAP_BACKUP_PASSPHRASE\nEOF"
)
RESTORE = (
    # A private temporary file (gpg verifies integrity before pg_restore reads anything), removed on
    # every exit, so a failed restore leaves no plaintext and cannot block the next one.
    "set -eu; umask 077; F=$(mktemp /tmp/restore.XXXXXX); trap 'rm -f \"$F\"' EXIT; "
    'gpg --batch --yes --quiet --pinentry-mode loopback --decrypt --passphrase-fd 3 -o "$F" '
    "3<<EOF\n$CRIMEMAP_BACKUP_PASSPHRASE\nEOF\n"
    'pg_restore -U postgres -h 127.0.0.1 -d "$TARGET" --exit-on-error "$F"'
)


@dataclass(frozen=True)
class Server:
    container: str
    host: str
    port: int
    database: str
    superuser_password: str
    app_password: str

    def dsn(self, database: str | None = None, role: str = "postgres") -> str:
        password = self.superuser_password if role == "postgres" else self.app_password
        return psycopg.conninfo.make_conninfo(
            host=self.host,
            port=self.port,
            dbname=database or self.database,
            user=role,
            password=password,
        )


def docker(
    server: Server, script: str, env: dict[str, str], **kwargs: Any
) -> subprocess.CompletedProcess[bytes]:
    """Run a fixed script in the container; values are passed by environment variable name."""
    names = [arg for name in env for arg in ("-e", name)]
    return subprocess.run(  # noqa: S603 - fixed argument list, no host shell
        ["docker", "exec", "-i", *names, server.container, "bash", "-c", script],  # noqa: S607
        env={**os.environ, **env},
        check=False,
        **kwargs,
    )


def manifest_of(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    tables = conn.execute(
        "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY(%s) AND c.relkind IN ('r', 'p') AND NOT c.relispartition "
        "ORDER BY 1, 2",
        [list(SCHEMAS)],
    ).fetchall()
    counts = {}
    for schema, table in tables:
        query = sql.SQL("SELECT count(*) FROM {}.{}").format(
            sql.Identifier(schema), sql.Identifier(table)
        )
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- psycopg sql.Identifier quotes the name (no SQLAlchemy, no user input)
        counts[f"{schema}.{table}"] = conn.execute(query).fetchone()[0]  # type: ignore[index]
    version = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
    chain = conn.execute("SELECT ok, checked FROM audit.verify_chain()").fetchone()
    rls = conn.execute(
        "SELECT count(*) FILTER (WHERE relrowsecurity), count(*) FILTER (WHERE relforcerowsecurity) "
        "FROM pg_class WHERE relnamespace::regnamespace::text = ANY(%s)",
        [list(SCHEMAS)],
    ).fetchone()
    policies = conn.execute(
        "SELECT count(*) FROM pg_policies WHERE schemaname = ANY(%s)", [list(SCHEMAS)]
    ).fetchone()
    if version is None or chain is None or rls is None or policies is None:
        raise SystemExit("manifest query returned no row")
    return {
        "alembic_version": version[0],
        "tables": counts,
        "audit_chain": {"ok": bool(chain[0]), "checked": int(chain[1])},
        "rls_enabled_tables": int(rls[0]),
        "rls_forced_tables": int(rls[1]),
        "policies": int(policies[0]),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def backup(server: Server, passphrase: str, out: Path) -> Path:
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = out / f"{server.database}-{stamp}.dump.gpg"
    with psycopg.connect(server.dsn()) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]  # type: ignore[index]
        manifest = manifest_of(conn)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as sink:
            result = docker(
                server,
                DUMP,
                {
                    "DB": server.database,
                    "SNAPSHOT": snapshot,
                    "PGPASSWORD": server.superuser_password,
                    "CRIMEMAP_BACKUP_PASSPHRASE": passphrase,
                },
                stdout=sink,
                stderr=subprocess.PIPE,
            )
        conn.rollback()
    if result.returncode != 0 or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise SystemExit(f"backup failed: {result.stderr.decode(errors='replace')[-500:]}")
    manifest.update(
        created_at=datetime.now(UTC).isoformat(),
        database=server.database,
        file=target.name,
        bytes=target.stat().st_size,
        sha256=sha256(target),
        format="pg_dump custom format, GnuPG symmetric AES-256 (MDC), S2K SHA-512",
        classification="DEMO / SYNTHETIC DATA",
    )
    meta = target.with_name(target.name + ".manifest.json")
    fd = os.open(meta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    return target


def restore_test(
    server: Server, passphrase: str, file: Path, *, keep: bool = False
) -> dict[str, Any]:
    manifest = json.loads(file.with_name(file.name + ".manifest.json").read_text())
    scratch = f"{server.database}_restore_test"
    if not SCRATCH.match(scratch) or scratch == server.database:
        raise SystemExit("refusing: the scratch database name is not a restore-test name")
    report: dict[str, Any] = {
        "file": file.name,
        "started_at": datetime.now(UTC).isoformat(),
        "scratch_database": scratch,
        "checks": {},
    }
    checks: dict[str, Any] = report["checks"]
    checks["sha256_matches_manifest"] = sha256(file) == manifest["sha256"]
    if not checks["sha256_matches_manifest"]:
        report["passed"] = False
        return report

    with psycopg.connect(server.dsn(), autocommit=True) as conn:
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- psycopg sql.Identifier quotes the name (no SQLAlchemy, no user input)
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch))
        )
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- psycopg sql.Identifier quotes the name (no SQLAlchemy, no user input)
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
    try:
        with file.open("rb") as source:
            result = docker(
                server,
                RESTORE,
                {
                    "TARGET": scratch,
                    "PGPASSWORD": server.superuser_password,
                    "CRIMEMAP_BACKUP_PASSPHRASE": passphrase,
                },
                stdin=source,
                capture_output=True,
            )
        checks["decrypt_and_restore"] = result.returncode == 0
        if result.returncode != 0:
            report["error"] = result.stderr.decode(errors="replace")[-500:]
            report["passed"] = False
            return report

        with psycopg.connect(server.dsn(scratch)) as conn:
            restored = manifest_of(conn)
        checks["alembic_version"] = restored["alembic_version"] == manifest["alembic_version"]
        mismatched = {
            t: {"expected": n, "restored": restored["tables"].get(t)}
            for t, n in manifest["tables"].items()
            if restored["tables"].get(t) != n
        }
        checks["table_counts"] = not mismatched and set(restored["tables"]) == set(
            manifest["tables"]
        )
        if mismatched:
            report["mismatched_tables"] = mismatched
        report["tables_compared"] = len(manifest["tables"])
        report["rows_compared"] = sum(manifest["tables"].values())
        checks["audit_chain"] = restored["audit_chain"] == manifest["audit_chain"] and bool(
            restored["audit_chain"]["ok"]
        )
        checks["row_level_security"] = (
            restored["rls_enabled_tables"] == manifest["rls_enabled_tables"]
            and restored["rls_forced_tables"] == manifest["rls_forced_tables"]
            and restored["policies"] == manifest["policies"]
        )
        # The application role works on the restored copy, and row-level security still applies.
        with psycopg.connect(server.dsn(scratch, role="crimemap_app")) as app:
            unscoped = app.execute("SELECT count(*) FROM core.incident_record").fetchone()[0]  # type: ignore[index]
            app.execute("SELECT set_config('app.scope_global', 'on', true)")
            scoped = app.execute("SELECT count(*) FROM core.incident_record").fetchone()[0]  # type: ignore[index]
        incidents = manifest["tables"].get("core.incident_record", 0)
        checks["app_role_without_scope_sees_nothing"] = unscoped == 0
        checks["app_role_with_scope_sees_all_but_highly_restricted"] = (
            0 < scoped <= incidents or incidents == 0
        )
    finally:
        if not keep:
            with psycopg.connect(server.dsn(), autocommit=True) as conn:
                # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query -- psycopg sql.Identifier quotes the name (no SQLAlchemy, no user input)
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(scratch)
                    )
                )
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["passed"] = all(v is True for v in checks.values())
    return report


def server_from_env() -> Server:
    def need(name: str) -> str:
        value = os.environ.get(name, "")
        if not value or value == "change-me":
            raise SystemExit(f"{name} is not set (source .env first)")
        return value

    return Server(
        container=os.environ.get("CRIMEMAP_PG_CONTAINER", "crimemap-postgres-1"),
        host="127.0.0.1",
        port=int(os.environ.get("CRIMEMAP_PG_HOST_PORT", "55432")),
        database=need("CRIMEMAP_DB_NAME"),
        superuser_password=need("POSTGRES_SUPERUSER_PASSWORD"),
        app_password=need("CRIMEMAP_APP_PASSWORD"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out", type=Path, default=REPO / "backups")
    r = sub.add_parser("restore-test")
    r.add_argument("file", type=Path)
    r.add_argument("--keep", action="store_true", help="keep the scratch database for inspection")
    args = parser.parse_args()
    server = server_from_env()
    passphrase = os.environ.get("CRIMEMAP_BACKUP_PASSPHRASE", "")
    if len(passphrase) < 32:
        raise SystemExit(
            "CRIMEMAP_BACKUP_PASSPHRASE must be set (32+ characters; scripts/dev_bootstrap.sh)"
        )
    if args.command == "backup":
        path = backup(server, passphrase, args.out)
        print(f"backup written: {path} (+ .manifest.json)")
    else:
        report = restore_test(server, passphrase, args.file, keep=args.keep)
        out = args.file.with_name(args.file.name + ".restore-test.json")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # private from creation
        with os.fdopen(fd, "w") as f:
            json.dump(report, f, indent=2, sort_keys=True)
        print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, indent=2))
        sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
