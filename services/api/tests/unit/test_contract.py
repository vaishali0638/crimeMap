"""Canonical source-record contract v1 (docs/integration-contracts.md §3)."""

from typing import Any

import pytest
from pydantic import ValidationError

from crimemap_api.ingest.contract import SourceRecord


def record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_system": "DEMO_SYNTHETIC",
        "source_record_reference": "DEMO-PDY-2026-000001",
        "source_version": 1,
        "record_type": "CASE",
        "official_offence_act": "Demo Penal Code (synthetic)",
        "official_offence_section": "D-201",
        "official_offence_name": "Theft (synthetic)",
        "occurrence_start_at": "2026-09-01T20:00:00+05:30",
        "occurrence_end_at": "2026-09-01T21:00:00+05:30",
        "reported_at": "2026-09-02T09:00:00+05:30",
        "reporting_station_code": "PDY-S01",
        "status": "REGISTERED",
        "location": {
            "lat": 11.93,
            "lon": 79.80,
            "precision": "EXACT",
            "method": "SOURCE_COORDINATE",
            "confidence": 0.9,
        },
        "sensitivity_classification": "RESTRICTED",
    }
    base.update(overrides)
    return base


def test_valid_case_record() -> None:
    r = SourceRecord.model_validate(record())
    assert r.classification_code == "D-201"


def test_unknown_fields_are_dropped_and_counted() -> None:
    r = SourceRecord.model_validate(record(victim_name="must not be kept", extra_field=1))
    assert r.dropped_fields == ["extra_field", "victim_name"]
    assert "victim_name" not in r.model_dump()


@pytest.mark.parametrize(
    "overrides",
    [
        {"record_type": "FIR_CREATE"},
        {"reported_at": None},
        {"reported_at": "2026-09-02T09:00:00"},  # no timezone
        {"source_version": 0},
        {"official_offence_section": None},  # CASE needs an offence section
        {"occurrence_end_at": "2026-09-01T19:00:00+05:30"},  # ends before it starts
        {
            "location": {
                "lat": 191,
                "lon": 79.8,
                "precision": "EXACT",
                "method": "SOURCE_COORDINATE",
                "confidence": 0.9,
            }
        },
        {
            "location": {
                "lat": 11.9,
                "lon": 79.8,
                "precision": "EXACT",
                "method": "SOURCE_COORDINATE",
                "confidence": 1.5,
            }
        },
        {"sensitivity_classification": "SECRET"},
        {"source_record_reference": "x" * 200},
    ],
)
def test_invalid_records_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceRecord.model_validate(record(**overrides))


def test_non_case_records_use_an_event_type_code() -> None:
    r = SourceRecord.model_validate(
        record(
            record_type="ERSS_CALL",
            official_offence_act=None,
            official_offence_section=None,
            official_offence_name=None,
            event_type_code="E-01",
            status="RECEIVED",
        )
    )
    assert r.classification_code == "E-01"


def test_missing_location_is_allowed() -> None:
    r = SourceRecord.model_validate(
        record(
            location={
                "lat": None,
                "lon": None,
                "precision": "UNKNOWN",
                "method": "NONE",
                "confidence": None,
            }
        )
    )
    assert r.location is not None
    assert r.location.lat is None


@pytest.mark.parametrize("version", ["2", 2.0, True, 2**31, 0])
def test_versions_must_be_plain_integers_in_range(version: object) -> None:
    """Lax values used to pass validation and then abort the whole batch in SQL (audit H1)."""
    with pytest.raises(ValidationError):
        SourceRecord.model_validate(record(source_version=version))


def test_residential_flag_must_be_a_boolean() -> None:
    data = record()
    data["location"] = {**data["location"], "is_residential": 1.0}
    with pytest.raises(ValidationError):
        SourceRecord.model_validate(data)


def test_normalised_record_excludes_unknown_fields() -> None:
    parsed = SourceRecord.model_validate({**record(), "evil_pii": "x"})
    assert "evil_pii" not in parsed.model_dump(mode="json")
    assert parsed.dropped_fields == ["evil_pii"]
