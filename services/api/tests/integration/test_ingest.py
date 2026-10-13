"""Phase 2 acceptance: ingestion is idempotent, spatial joins are correct, data-quality issues are
generated for every injected defect, lineage and audit are complete (docs/architecture.md §5)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from crimemap_worker.ingest import check_source_health
from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.demo.generator import GeneratorConfig, generate, write_batches

from .conftest import KEY, Database, Ingested, N, ingest_files, run

pytestmark = pytest.mark.integration


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    """Run as the migrator (RLS policy for the owner allows full read)."""
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


def one(database: Database, query: str, *params: object) -> Any:
    return sql(database, query, *params)[0][0]


MAIN = "(SELECT id FROM core.source_system WHERE code = 'DEMO_SYNTHETIC')"


# --- Acceptance: re-running ingest produces no duplicates ---------------------------------------


def test_every_valid_record_becomes_exactly_one_incident(ingested: Ingested) -> None:
    invalid = len(ingested.manifest["defects"]["schema_invalid"])
    assert (
        one(
            ingested.database,
            f"SELECT count(*) FROM core.incident_record WHERE source_system_id = {MAIN}",
        )
        == N - invalid
    )


def test_rerunning_the_same_batches_adds_nothing(ingested: Ingested) -> None:
    counts = "SELECT (SELECT count(*) FROM core.incident_record), (SELECT count(*) FROM staging.raw_ingest), (SELECT count(*) FROM core.source_record_link), (SELECT count(*) FROM core.data_quality_issue)"
    before = sql(ingested.database, counts)
    again = run(ingest_files(ingested.database, ingested.files))
    assert all(r.status == "ALREADY_PROCESSED" for r in again)
    assert sql(ingested.database, counts) == before


def test_resending_records_in_a_new_batch_counts_them_as_duplicates(
    ingested: Ingested, tmp_path: Path
) -> None:
    envelope = json.loads(ingested.files[0].read_text())["envelope"]
    data = type(
        "Out", (), {"records": envelope["records"][:50], "manifest": {"generator": {"seed": 999}}}
    )()
    [path] = write_batches(data, tmp_path, KEY, batch_size=100, extracted_at=datetime.now(UTC))  # type: ignore[arg-type]
    before = one(ingested.database, "SELECT count(*) FROM core.incident_record")
    [result] = run(ingest_files(ingested.database, [path]))
    assert (result.accepted, result.duplicates) == (0, 50)
    assert one(ingested.database, "SELECT count(*) FROM core.incident_record") == before


def test_batch_counts_reconcile(ingested: Ingested) -> None:
    for r in ingested.results:
        assert r.accepted + r.duplicates + r.rejected == r.record_count
    assert (
        one(
            ingested.database,
            "SELECT coalesce(sum(unexplained), 0) FROM core.ingest_reconciliation",
        )
        == 0
    )


# --- Acceptance: spatial joins assign records to valid synthetic boundaries ----------------------


def test_located_incidents_lie_in_their_assigned_beat_station_and_region(
    ingested: Ingested,
) -> None:
    rows = sql(
        ingested.database,
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE ST_Covers(bb.geometry, l.geometry)),
               count(*) FILTER (WHERE ST_Covers(sb.geometry, l.geometry)),
               count(*) FILTER (WHERE ST_Covers(rb.geometry, l.geometry)),
               count(*) FILTER (WHERE bo.parent_id = i.station_id AND so.parent_id = i.region_id)
        FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id
        JOIN core.current_boundary bb ON bb.org_unit_id = i.beat_id
        JOIN core.current_boundary sb ON sb.org_unit_id = i.station_id
        JOIN core.current_boundary rb ON rb.org_unit_id = i.region_id
        JOIN core.organizational_unit bo ON bo.id = i.beat_id
        JOIN core.organizational_unit so ON so.id = i.station_id
        WHERE i.source_system_id = {MAIN}
        """,
    )
    total, in_beat, in_station, in_region, hierarchy = rows[0]
    assert total > 0.9 * N
    assert total == in_beat == in_station == in_region == hierarchy


def test_inside_points_are_all_assigned_a_beat(ingested: Ingested) -> None:
    unassigned = one(
        ingested.database,
        f"SELECT count(*) FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id "
        f"WHERE i.source_system_id = {MAIN} AND l.geometry IS NOT NULL AND i.beat_id IS NULL "
        "AND EXISTS (SELECT 1 FROM core.current_boundary b WHERE b.unit_type = 'REGION' AND ST_Covers(b.geometry, l.geometry))",
    )
    assert unassigned == 0


def test_unlocated_incidents_still_get_a_region_from_the_reporting_station(
    ingested: Ingested,
) -> None:
    missing = one(
        ingested.database,
        f"SELECT count(*) FROM core.incident_record WHERE source_system_id = {MAIN} AND region_id IS NULL",
    )
    assert missing == 0


# --- Acceptance: data-quality issues are generated ------------------------------------------------

DEFECT_TO_ISSUE = {
    "missing_location": "MISSING_LOCATION",
    "low_confidence_location": "LOW_CONFIDENCE_LOCATION",
    "missing_occurrence_time": "MISSING_OCCURRENCE_TIME",
    "invalid_category_mapping": "INVALID_CATEGORY_MAPPING",
    "outside_jurisdiction": "OUTSIDE_JURISDICTION",
}


@pytest.mark.parametrize("defect", list(DEFECT_TO_ISSUE))
def test_every_injected_defect_raises_its_issue(ingested: Ingested, defect: str) -> None:
    invalid = set(ingested.manifest["defects"]["schema_invalid"])
    refs = [r for r in ingested.manifest["defects"][defect] if r not in invalid]
    assert refs
    flagged = {
        row[0]
        for row in sql(
            ingested.database,
            "SELECT i.source_record_reference FROM core.data_quality_issue q "
            "JOIN core.incident_record i ON i.id = q.incident_id WHERE q.issue_type = %s "
            f"AND i.source_system_id = {MAIN}",
            DEFECT_TO_ISSUE[defect],
        )
    }
    assert set(refs) <= flagged


def test_injected_duplicates_are_flagged(ingested: Ingested) -> None:
    invalid = set(ingested.manifest["defects"]["schema_invalid"])
    refs = [r for r in ingested.manifest["defects"]["possible_duplicate"] if r not in invalid]
    flagged = {
        row[0]
        for row in sql(
            ingested.database,
            "SELECT i.source_record_reference FROM core.data_quality_issue q JOIN core.incident_record i "
            "ON i.id = q.incident_id WHERE q.issue_type = 'POSSIBLE_DUPLICATE'",
        )
    }
    assert len(set(refs) & flagged) >= 0.9 * len(refs)


def test_clean_records_raise_few_issues(ingested: Ingested) -> None:
    defective = {r for refs in ingested.manifest["defects"].values() for r in refs}
    noisy = one(
        ingested.database,
        "SELECT count(DISTINCT i.source_record_reference) FROM core.data_quality_issue q "
        f"JOIN core.incident_record i ON i.id = q.incident_id WHERE i.source_system_id = {MAIN} "
        "AND NOT (i.source_record_reference = ANY(%s))",
        sorted(defective),
    )
    assert noisy <= 0.01 * N  # only natural near-duplicates in dense clusters


def test_quality_status_reflects_open_issues(ingested: Ingested) -> None:
    rows = dict(
        sql(
            ingested.database,
            "SELECT i.record_quality_status, count(*) FROM core.incident_record i GROUP BY 1",
        )
    )
    assert rows.get("FAILED", 0) > 0
    assert rows.get("WARNING", 0) > 0
    assert rows["OK"] > 0.8 * N


def test_issues_carry_territory_for_scoped_review(ingested: Ingested) -> None:
    assert (
        one(
            ingested.database,
            "SELECT count(*) FROM core.data_quality_issue WHERE subject_type = 'INCIDENT' AND region_id IS NULL",
        )
        == 0
    )


def test_stale_feed_raises_a_source_issue(ingested: Ingested) -> None:
    run(_health(ingested.database))
    status = one(
        ingested.database,
        "SELECT last_health_status FROM core.source_system WHERE code = 'DEMO_SYNTHETIC_LEGACY'",
    )
    assert status == "STALE"
    assert (
        one(
            ingested.database,
            "SELECT count(*) FROM core.data_quality_issue q JOIN core.source_system s ON s.id = q.subject_id "
            "WHERE q.issue_type = 'STALE_SOURCE' AND s.code = 'DEMO_SYNTHETIC_LEGACY' AND q.state = 'OPEN'",
        )
        == 1
    )
    assert (
        one(
            ingested.database,
            "SELECT last_health_status FROM core.source_system WHERE code = 'DEMO_SYNTHETIC'",
        )
        == "OK"
    )


async def _health(database: Database) -> None:
    engine = create_async_engine(database.worker_async)
    try:
        await check_source_health(engine, environment="DEMO")
    finally:
        await engine.dispose()


# --- Dead letters, versions, lineage, restricted data, audit --------------------------------------


def test_schema_invalid_records_go_to_the_dead_letter_queue(ingested: Ingested) -> None:
    rows = sql(
        ingested.database,
        "SELECT r.payload->>'source_record_reference', d.error_code, d.error_detail FROM staging.dead_letter d "
        "JOIN staging.raw_ingest r ON r.id = d.raw_ingest_id",
    )
    assert {r[0] for r in rows} == set(ingested.manifest["defects"]["schema_invalid"])
    assert all(r[1] == "SCHEMA_INVALID" for r in rows)
    assert all("SYNTHETIC-REF" not in r[2] for r in rows)  # no payload values echoed


def test_newer_versions_update_the_incident_and_keep_history(ingested: Ingested) -> None:
    updates = ingested.manifest["defects"]["version_update"]
    rows = sql(
        ingested.database,
        "SELECT i.source_version, i.status_code, (SELECT count(*) FROM core.source_record_link l "
        "WHERE l.source_record_reference = i.source_record_reference AND l.source_system_id = i.source_system_id) "
        f"FROM core.incident_record i WHERE i.source_system_id = {MAIN} AND i.source_record_reference = ANY(%s)",
        updates,
    )
    assert len(rows) == len(updates)
    assert all(
        v == 2 and status in ("DETECTED", "CLOSED") and links == 2 for v, status, links in rows
    )


def test_every_incident_has_lineage_to_its_raw_record(ingested: Ingested) -> None:
    orphans = one(
        ingested.database,
        "SELECT count(*) FROM core.incident_record i WHERE NOT EXISTS (SELECT 1 FROM core.source_record_link l "
        "JOIN staging.raw_ingest r ON r.id = l.raw_ingest_id WHERE l.incident_id = i.id AND l.content_hash = r.content_hash)",
    )
    assert orphans == 0


def test_sensitive_details_go_only_to_the_restricted_schema(ingested: Ingested) -> None:
    restricted = one(ingested.database, "SELECT count(*) FROM restricted.sensitive_source_detail")
    assert restricted > 0
    leaked = one(
        ingested.database,
        "SELECT count(*) FROM core.incident_location WHERE location_text LIKE 'SYNTHETIC-REF%%' OR locality LIKE 'SYNTHETIC-REF%%'",
    )
    assert leaked == 0


def test_incidents_are_flagged_demo_and_classified(ingested: Ingested) -> None:
    rows = sql(
        ingested.database,
        "SELECT bool_and(is_demo), count(*) FILTER (WHERE access_classification = 'HIGHLY_RESTRICTED') FROM core.incident_record",
    )
    assert rows[0][0] is True
    assert rows[0][1] > 0


def test_each_batch_is_audited_and_the_chain_holds(ingested: Ingested) -> None:
    events = one(
        ingested.database, "SELECT count(*) FROM audit.audit_event WHERE action = 'INGEST_BATCH'"
    )
    assert events >= len(ingested.files) + len(ingested.legacy_files)
    assert one(ingested.database, "SELECT ok FROM audit.verify_chain()") is True


def test_tampered_batch_is_rejected_without_staging_anything(
    ingested: Ingested, tmp_path: Path
) -> None:
    raw = json.loads(ingested.files[1].read_text())
    raw["envelope"]["batch_ref"] = "tampered-batch"
    raw["envelope"]["records"][0]["status"] = "CLOSED"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw))
    before = one(ingested.database, "SELECT count(*) FROM staging.raw_ingest")
    [result] = run(ingest_files(ingested.database, [path]))
    assert result.status == "REJECTED_SIGNATURE"
    assert one(ingested.database, "SELECT count(*) FROM staging.raw_ingest") == before
    assert (
        one(
            ingested.database,
            "SELECT count(*) FROM audit.audit_event WHERE action = 'INGEST_REJECTED'",
        )
        >= 1
    )


def test_demo_sources_are_refused_in_production(ingested: Ingested) -> None:
    with pytest.raises(PermissionError, match="PRODUCTION"):
        run(ingest_files(ingested.database, ingested.files[:1], environment="PRODUCTION"))


def test_ingest_cli_reruns_a_directory_without_changes(
    ingested: Ingested, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from crimemap_worker.ingest_cli import main

    before = one(ingested.database, "SELECT count(*) FROM core.incident_record")
    monkeypatch.setenv("CRIMEMAP_WORKER_DATABASE_URL", ingested.database.worker_async)
    monkeypatch.setenv("CRIMEMAP_DEMO_SIGNING_KEY", KEY.decode())
    monkeypatch.setenv("CRIMEMAP_WORKER_ENVIRONMENT", "demo")  # any letter case
    assert main([str(ingested.files[0].parent)]) == 0
    assert "ALREADY_PROCESSED" in capsys.readouterr().out
    assert one(ingested.database, "SELECT count(*) FROM core.incident_record") == before


def test_ingest_cli_refuses_to_run_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from crimemap_worker.ingest_cli import main

    monkeypatch.setenv("CRIMEMAP_WORKER_DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
    monkeypatch.setenv("CRIMEMAP_DEMO_SIGNING_KEY", "short")
    assert main(["/nonexistent"]) == 2


@pytest.mark.parametrize("source", ["CCTNS", "ERSS_112", "ICJS", "EDAR", "ECHALLAN", "CCTV_ICCC"])
def test_unapproved_government_sources_cannot_ingest(
    ingested: Ingested, tmp_path: Path, source: str
) -> None:
    """Interface stubs exist for each named system, but none may load data until approved."""
    data = generate(GeneratorConfig(records=5, seed=11, source_system=source))
    files = write_batches(
        data, tmp_path, KEY, batch_size=5, extracted_at=datetime.now(UTC), source_system=source
    )
    with pytest.raises(PermissionError, match="not approved"):
        run(ingest_files(ingested.database, files))
    assert (
        one(
            ingested.database,
            "SELECT count(*) FROM staging.ingest_batch b JOIN core.source_system s "
            "ON s.id = b.source_system_id WHERE s.code = %s",
            source,
        )
        == 0
    )


@pytest.mark.parametrize("value", [None, "", "prod", "TEST"])
def test_ingest_cli_refuses_an_unset_or_unknown_environment(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """Audit M4: an unset environment used to mean DEMO, so a misconfigured host ran demo feeds."""
    from crimemap_worker.ingest_cli import main

    monkeypatch.setenv("CRIMEMAP_WORKER_DATABASE_URL", "postgresql+asyncpg://x@localhost/x")
    monkeypatch.setenv("CRIMEMAP_DEMO_SIGNING_KEY", "k" * 32)
    if value is None:
        monkeypatch.delenv("CRIMEMAP_WORKER_ENVIRONMENT", raising=False)
    else:
        monkeypatch.setenv("CRIMEMAP_WORKER_ENVIRONMENT", value)
    with pytest.raises(SystemExit, match="environment must be set"):
        main(["/nonexistent"])


def test_ingest_cli_ignores_symbolic_links(
    ingested: Ingested,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # Phase 8 audit: the directory ingester followed links, unlike the scheduled inbox.
    from crimemap_worker.ingest_cli import main

    (tmp_path / "linked.json").symlink_to(ingested.files[0])
    monkeypatch.setenv("CRIMEMAP_WORKER_DATABASE_URL", ingested.database.worker_async)
    monkeypatch.setenv("CRIMEMAP_DEMO_SIGNING_KEY", KEY.decode())
    monkeypatch.setenv("CRIMEMAP_WORKER_ENVIRONMENT", "DEMO")
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ALREADY_PROCESSED" not in out
