"""Synthetic incident generator (docs/demo-data.md): deterministic, valid, realistic, labelled."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from crimemap_api.demo.generator import CATEGORY_OF_CODE, GeneratorConfig, generate, write_batches
from crimemap_api.demo.geography import REGIONS, point_in_ring, station_seeds
from crimemap_api.ingest.contract import SourceRecord
from crimemap_api.ingest.signing import BatchSignatureError, read_batch

CONFIG = GeneratorConfig(records=5000, seed=42)
KEY = b"0" * 32


@pytest.fixture(scope="module")
def output():  # type: ignore[no-untyped-def]
    return generate(CONFIG)


def test_generation_is_deterministic(output) -> None:  # type: ignore[no-untyped-def]
    again = generate(CONFIG)
    assert again.records == output.records
    assert again.manifest == output.manifest


def test_different_seed_gives_different_data(output) -> None:  # type: ignore[no-untyped-def]
    other = generate(GeneratorConfig(records=200, seed=43))
    assert other.records[:5] != output.records[:5]


def test_record_count_includes_version_updates(output) -> None:  # type: ignore[no-untyped-def]
    updates = output.manifest["defects"]["version_update"]
    assert len(output.records) == CONFIG.records + len(updates)
    refs = Counter(r["source_record_reference"] for r in output.records)
    assert sorted(ref for ref, n in refs.items() if n == 2) == sorted(updates)


def test_valid_records_satisfy_the_contract_and_invalid_ones_do_not(output) -> None:  # type: ignore[no-untyped-def]
    invalid = set(output.manifest["defects"]["schema_invalid"])
    assert invalid
    for record in output.records:
        if record.get("source_record_reference") in invalid:
            with pytest.raises(ValidationError):
                SourceRecord.model_validate(record)
        else:
            SourceRecord.model_validate(record)


@pytest.mark.parametrize(
    ("defect", "rate"),
    [
        ("missing_location", 0.03),
        ("low_confidence_location", 0.05),
        ("missing_occurrence_time", 0.02),
        ("possible_duplicate", 0.01),
        ("invalid_category_mapping", 0.005),
        ("outside_jurisdiction", 0.005),
        ("schema_invalid", 0.002),
    ],
)
def test_defects_are_injected_at_configured_rates(output, defect: str, rate: float) -> None:  # type: ignore[no-untyped-def]
    n = len(output.manifest["defects"][defect])
    assert 0.5 * rate * CONFIG.records <= n <= 1.5 * rate * CONFIG.records


def _by_ref(output):  # type: ignore[no-untyped-def]
    return {r["source_record_reference"]: r for r in output.records if r.get("source_version") == 1}


def test_locations_are_inside_their_region_except_deliberate_defects(output) -> None:  # type: ignore[no-untyped-def]
    excluded = (
        set(output.manifest["defects"]["outside_jurisdiction"])
        | set(output.manifest["defects"]["missing_location"])
        | set(output.manifest["defects"]["schema_invalid"])
    )
    region_of = {s.code: code for code in REGIONS for s in station_seeds(code)}
    for ref, r in _by_ref(output).items():
        if ref in excluded:
            continue
        loc = r["location"]
        ring = REGIONS[region_of[r["reporting_station_code"]]].outline
        assert point_in_ring(loc["lon"], loc["lat"], ring), ref


def test_outside_jurisdiction_points_are_really_outside(output) -> None:  # type: ignore[no-untyped-def]
    records = _by_ref(output)
    for ref in output.manifest["defects"]["outside_jurisdiction"]:
        loc = records[ref]["location"]
        assert not any(point_in_ring(loc["lon"], loc["lat"], r.outline) for r in REGIONS.values())


def test_missing_location_records_have_no_coordinates(output) -> None:  # type: ignore[no-untyped-def]
    records = _by_ref(output)
    for ref in output.manifest["defects"]["missing_location"]:
        loc = records[ref]["location"]
        assert loc["lat"] is None
        assert loc["lon"] is None
        assert loc["precision"] == "UNKNOWN"


def test_region_mix_is_weighted_towards_puducherry(output) -> None:  # type: ignore[no-untyped-def]
    regions = Counter(r["reporting_station_code"][:3] for r in _by_ref(output).values())
    total = sum(regions.values())
    assert regions["PDY"] / total == pytest.approx(0.55, abs=0.04)
    assert set(regions) == {"PDY", "KKL", "MAH", "YAN"}


def test_times_fall_inside_the_window_and_reports_follow_occurrence(output) -> None:  # type: ignore[no-untyped-def]
    end = CONFIG.end
    start = end - timedelta(days=30 * CONFIG.months + 31)
    invalid = set(output.manifest["defects"]["schema_invalid"])
    for ref, r in _by_ref(output).items():
        if ref in invalid:
            continue
        reported = datetime.fromisoformat(r["reported_at"])
        assert start <= reported <= end + timedelta(days=30)
        if r["occurrence_start_at"] is not None:
            occ_start = datetime.fromisoformat(r["occurrence_start_at"])
            occ_end = datetime.fromisoformat(r["occurrence_end_at"])
            assert start <= occ_start <= occ_end <= reported


def test_hour_of_day_pattern_is_not_uniform(output) -> None:  # type: ignore[no-untyped-def]
    hours = Counter(
        datetime.fromisoformat(r["occurrence_start_at"]).astimezone(UTC).hour
        for r in _by_ref(output).values()
        if r.get("occurrence_start_at")
    )
    assert max(hours.values()) > 2 * min(hours.values())


def test_sensitive_categories_are_restricted_with_synthetic_tokens_only(output) -> None:  # type: ignore[no-untyped-def]
    def category(r):  # type: ignore[no-untyped-def]
        return CATEGORY_OF_CODE.get(
            r.get("official_offence_section") or r.get("event_type_code") or ""
        )

    sensitive = [
        r
        for r in _by_ref(output).values()
        if category(r) in ("CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN")
    ]
    assert sensitive
    for r in sensitive:
        assert r["sensitivity_classification"] in ("RESTRICTED", "HIGHLY_RESTRICTED")
        assert re.fullmatch(r"SYNTHETIC-REF-\d{6}", r["restricted"]["victim_reference_token"])


def test_references_and_labels_are_clearly_synthetic(output) -> None:  # type: ignore[no-untyped-def]
    for r in output.records:
        assert re.fullmatch(r"DEMO-(PDY|KKL|MAH|YAN)-\d{4}-\d{6}", r["source_record_reference"])
        if r.get("official_offence_act"):
            assert "synthetic" in r["official_offence_act"].lower()


PII_PATTERNS = {
    "indian_mobile": r"(?<![\d-])(?:\+91[ -]?)?[6-9]\d{9}(?!\d)",
    "aadhaar_like": r"(?<![\d-])\d{4} ?\d{4} ?\d{4}(?![\d-])",
    "pan_like": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    "vehicle_registration": r"\b(?:PY|TN|KL|AP)[ -]?\d{2}[ -]?[A-Z]{1,2}[ -]?\d{4}\b",
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
}


@pytest.mark.parametrize("name", list(PII_PATTERNS))
def test_output_contains_nothing_that_looks_like_personal_data(output, name: str) -> None:  # type: ignore[no-untyped-def]
    text = json.dumps(output.records)
    assert not re.search(PII_PATTERNS[name], text), name


def test_batches_are_signed_and_tampering_is_detected(output, tmp_path) -> None:  # type: ignore[no-untyped-def]
    paths = write_batches(output, tmp_path, KEY, batch_size=2000, extracted_at=CONFIG.end)
    assert len(paths) == 3
    batch = read_batch(paths[0], KEY)
    assert batch.envelope["record_count"] == len(batch.envelope["records"]) == 2000
    raw = json.loads(paths[1].read_text())
    raw["envelope"]["records"][0]["status"] = "CLOSED"
    paths[1].write_text(json.dumps(raw))
    with pytest.raises(BatchSignatureError):
        read_batch(paths[1], KEY)
    with pytest.raises(BatchSignatureError):
        read_batch(paths[2], b"1" * 32)


def test_manifest_records_totals_for_reconciliation(output) -> None:  # type: ignore[no-untyped-def]
    totals = output.manifest["daily_totals"]
    assert sum(totals.values()) == len(output.records)
    assert output.manifest["generator"] == {"version": "1", "seed": 42, "records": 5000}
