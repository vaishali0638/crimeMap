"""Adapter architecture, scheduled inbox ingestion and the daily reconciliation report.

These tests never add incidents to the shared session dataset: the inbox test re-delivers batches
that were already ingested (plus one tampered file), and the reconciliation discrepancy is created
inside a transaction that is rolled back.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from crimemap_worker.adapters import (
    ADAPTERS,
    AdapterNotApproved,
    SignedFileDropAdapter,
    adapter_for,
)
from crimemap_worker.ingest import ingest_inbox, reconcile_day
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import KEY, Database, Ingested, run

pytestmark = pytest.mark.integration

GOVERNMENT = ["CCTNS", "ERSS_112", "ICJS", "EDAR", "ECHALLAN", "CCTV_ICCC"]


def test_every_registered_source_has_an_adapter() -> None:
    assert set(GOVERNMENT) | {"DEMO_SYNTHETIC", "DEMO_SYNTHETIC_LEGACY"} <= set(ADAPTERS)


@pytest.mark.parametrize("code", GOVERNMENT)
def test_government_adapters_are_stubs_that_refuse_to_run(code: str, tmp_path: Path) -> None:
    with pytest.raises(AdapterNotApproved, match=code):
        adapter_for(code, "APPROVED", tmp_path).pending_batches()


def test_an_adapter_is_refused_when_its_source_is_not_approved(tmp_path: Path) -> None:
    with pytest.raises(AdapterNotApproved, match="NOT_APPROVED"):
        adapter_for("DEMO_SYNTHETIC", "NOT_APPROVED", tmp_path)


def test_file_drop_adapter_lists_only_batch_files_in_order(tmp_path: Path) -> None:
    inbox = tmp_path / "demo_synthetic"
    inbox.mkdir()
    for name in ("b-0002.json", "b-0001.json", "b-manifest.json", "notes.txt"):
        (inbox / name).write_text("{}")
    adapter = SignedFileDropAdapter("DEMO_SYNTHETIC", tmp_path)
    assert [p.name for p in adapter.pending_batches()] == ["b-0001.json", "b-0002.json"]


def test_file_drop_adapter_ignores_symbolic_links(tmp_path: Path) -> None:
    # A link in the drop zone must not make the worker read (or later move) a file elsewhere.
    inbox = tmp_path / "demo_synthetic"
    inbox.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (inbox / "b-0001.json").write_text("{}")
    (inbox / "b-0002.json").symlink_to(outside)
    (inbox / "sub.json").mkdir()
    adapter = SignedFileDropAdapter("DEMO_SYNTHETIC", tmp_path)
    assert [p.name for p in adapter.pending_batches()] == ["b-0001.json"]


def test_inbox_ingestion_files_processed_and_rejected_batches(
    ingested: Ingested, tmp_path: Path
) -> None:
    inbox = tmp_path / "inbox" / "demo_synthetic_legacy"
    inbox.mkdir(parents=True)
    for f in ingested.legacy_files:
        shutil.copy(f, inbox / f.name)
    tampered = json.loads(ingested.legacy_files[0].read_text())
    tampered["envelope"]["records"][0]["source_version"] = 99
    (inbox / "zz-tampered.json").write_text(json.dumps(tampered))

    async def _go() -> dict[str, int]:
        engine = create_async_engine(ingested.database.worker_async)
        try:
            return await ingest_inbox(engine, tmp_path / "inbox", KEY, environment="DEMO")
        finally:
            await engine.dispose()

    summary = run(_go())
    assert summary["ALREADY_PROCESSED"] == len(ingested.legacy_files)
    assert summary["REJECTED_SIGNATURE"] == 1
    assert sorted(p.name for p in (inbox / "processed").iterdir()) == sorted(
        f.name for f in ingested.legacy_files
    )
    assert [p.name for p in (inbox / "rejected").iterdir()] == ["zz-tampered.json"]
    assert not list(inbox.glob("*.json"))


def _reconcile(database: Database, *, discrepancy: bool) -> tuple[list[dict[str, object]], int]:
    """Run the report as the worker; with ``discrepancy`` an unbalanced batch is added first.
    Everything is rolled back afterwards."""

    async def _go() -> tuple[list[dict[str, object]], int]:
        engine = create_async_engine(database.worker_async)
        try:
            async with engine.connect() as conn:
                tx = await conn.begin()
                if discrepancy:
                    await conn.execute(
                        text(
                            "INSERT INTO staging.ingest_batch (source_system_id, batch_ref, signature_valid, "
                            "record_count, accepted, status) SELECT id, 'test-unbalanced', true, 10, 7, 'PROCESSED' "
                            "FROM core.source_system WHERE code = 'DEMO_SYNTHETIC'"
                        )
                    )
                rows = await reconcile_day(conn, datetime.now(UTC).date(), environment="DEMO")
                issues = (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM core.data_quality_issue WHERE rule_code = 'DQ-REC-001' "
                            "AND state = 'OPEN'"
                        )
                    )
                ).scalar_one()
                await tx.rollback()
                return rows, int(issues)
        finally:
            await engine.dispose()

    return run(_go())


def test_daily_reconciliation_balances_and_is_audited(ingested: Ingested) -> None:
    rows, issues = _reconcile(ingested.database, discrepancy=False)
    assert rows
    assert all(r["unexplained"] == 0 for r in rows)
    assert issues == 0


def test_unexplained_records_raise_a_reconciliation_issue(ingested: Ingested) -> None:
    rows, issues = _reconcile(ingested.database, discrepancy=True)
    assert any(r["unexplained"] == 3 for r in rows)
    assert issues == 1


def test_reconciliation_report_is_written_to_the_audit_log(ingested: Ingested) -> None:
    async def _go() -> None:
        engine = create_async_engine(ingested.database.worker_async)
        try:
            async with engine.begin() as conn:
                await reconcile_day(conn, datetime.now(UTC).date(), environment="DEMO")
        finally:
            await engine.dispose()

    run(_go())
    with psycopg.connect(ingested.database.migrator) as conn:
        row = conn.execute(
            "SELECT after_meta FROM audit.audit_event WHERE action = 'RECONCILIATION_REPORT' "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0]["unexplained"] == 0
    assert row[0]["sources"] >= 1
