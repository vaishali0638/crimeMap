"""Encrypted backup and restore test (Phase 8): scripts/backup_restore.py against the test database.

The live Compose run is recorded in docs/phase-8-report.md; this repeats it on every test run and
adds the failure cases (wrong key, tampered file).
"""

import importlib.util
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import psycopg
import pytest

from .conftest import REPO, Database, Ingested

pytestmark = pytest.mark.integration


def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "backup_restore", REPO / "scripts" / "backup_restore.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_restore"] = module  # dataclasses look their module up while executing
    spec.loader.exec_module(module)
    return module


BR = tool()
PASSPHRASE = secrets.token_urlsafe(32)


@pytest.fixture(scope="module")
def server(ingested: Ingested, database: Database):  # type: ignore[no-untyped-def]
    return BR.Server(
        container=database.container,
        host=database.host,
        port=database.port,
        database="crimemap",
        superuser_password=database.superuser_password,
        app_password=database.app_password,
    )


@pytest.fixture(scope="module")
def backup(server, tmp_path_factory: pytest.TempPathFactory) -> Path:  # type: ignore[no-untyped-def]
    return BR.backup(server, PASSPHRASE, tmp_path_factory.mktemp("backups"))  # type: ignore[no-any-return]


def databases(database: Database) -> set[str]:
    with psycopg.connect(database.superuser) as conn:
        return {r[0] for r in conn.execute("SELECT datname FROM pg_database").fetchall()}


def test_the_backup_is_encrypted_and_private(backup: Path) -> None:
    data = backup.read_bytes()
    assert data[:1] == b"\x8c"  # OpenPGP symmetric-key session packet, not a pg_dump archive
    assert b"PGDMP" not in data
    assert b"incident_record" not in data
    for path in (backup, backup.with_name(backup.name + ".manifest.json")):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manifest = json.loads(backup.with_name(backup.name + ".manifest.json").read_text())
    assert manifest["tables"]["core.incident_record"] > 0
    assert manifest["audit_chain"]["ok"] is True


def test_the_restore_test_succeeds_and_cleans_up(server, backup: Path, database: Database) -> None:  # type: ignore[no-untyped-def]
    report = BR.restore_test(server, PASSPHRASE, backup)
    assert report["passed"] is True, report
    assert report["rows_compared"] > 1000
    assert all(report["checks"].values())
    assert "crimemap_restore_test" not in databases(database)


def test_a_wrong_passphrase_restores_nothing(server, backup: Path) -> None:  # type: ignore[no-untyped-def]
    report = BR.restore_test(server, "x" * 40, backup)
    assert report["passed"] is False
    assert report["checks"]["decrypt_and_restore"] is False


def test_a_tampered_file_is_refused_before_decryption(server, backup: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    copy = tmp_path / backup.name
    data = bytearray(backup.read_bytes())
    data[len(data) // 2] ^= 0xFF
    copy.write_bytes(bytes(data))
    (tmp_path / (backup.name + ".manifest.json")).write_bytes(
        backup.with_name(backup.name + ".manifest.json").read_bytes()
    )
    report = BR.restore_test(server, PASSPHRASE, copy)
    assert report["checks"] == {"sha256_matches_manifest": False}
    assert report["passed"] is False


def test_tampering_that_also_rewrites_the_manifest_fails_the_integrity_check(  # type: ignore[no-untyped-def]
    server, backup: Path, tmp_path: Path
) -> None:
    copy = tmp_path / backup.name
    data = bytearray(backup.read_bytes())
    data[len(data) // 2] ^= 0xFF
    copy.write_bytes(bytes(data))
    manifest = json.loads(backup.with_name(backup.name + ".manifest.json").read_text())
    manifest["sha256"] = BR.sha256(copy)
    (tmp_path / (backup.name + ".manifest.json")).write_text(json.dumps(manifest))
    report = BR.restore_test(server, PASSPHRASE, copy)
    assert report["checks"]["sha256_matches_manifest"] is True
    assert report["checks"]["decrypt_and_restore"] is False  # GnuPG's integrity check (MDC)
    assert report["passed"] is False


def container_tmp(database: Database) -> list[str]:
    out = subprocess.run(
        ["docker", "exec", database.container, "ls", "-A", "/tmp"],  # noqa: S108 - listing the container's /tmp
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split()
    return sorted(out)


def test_a_failed_restore_leaves_no_plaintext_and_does_not_block_the_next(  # type: ignore[no-untyped-def]
    server, backup: Path, tmp_path: Path, database: Database
) -> None:
    # Phase 8 audit: the decrypted dump was written to a fixed /tmp path and removed only on success.
    bogus = tmp_path / "bogus.dump.gpg"
    encrypted = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            "P",
            database.container,
            "sh",
            "-c",
            "gpg --batch --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 "
            "--passphrase-fd 3 -o - 3<<EOF\n$P\nEOF",
        ],
        input=b"not a pg_dump archive",
        capture_output=True,
        check=True,
        env={**os.environ, "P": PASSPHRASE},
    ).stdout
    bogus.write_bytes(encrypted)
    manifest = json.loads(backup.with_name(backup.name + ".manifest.json").read_text())
    manifest["sha256"] = BR.sha256(bogus)
    bogus.with_name(bogus.name + ".manifest.json").write_text(json.dumps(manifest))

    failed = BR.restore_test(server, PASSPHRASE, bogus)
    assert failed["checks"]["sha256_matches_manifest"] is True
    assert failed["checks"]["decrypt_and_restore"] is False
    # No decrypted file left behind (this or any earlier failed restore in this module).
    assert [f for f in container_tmp(database) if "dump" in f or f.startswith("restore")] == []
    assert BR.restore_test(server, PASSPHRASE, backup)["passed"] is True


def test_a_dump_failure_fails_the_backup_pipeline(server) -> None:  # type: ignore[no-untyped-def]
    # Phase 8 audit: `pg_dump | gpg` without pipefail reported gpg's success for a failed dump.
    result = BR.docker(
        server,
        BR.DUMP,
        {
            "DB": server.database,
            "SNAPSHOT": "00000000-00000000-1",  # no such exported snapshot: pg_dump fails
            "PGPASSWORD": server.superuser_password,
            "CRIMEMAP_BACKUP_PASSPHRASE": PASSPHRASE,
        },
        capture_output=True,
    )
    assert result.returncode != 0
