"""Canonical source-record contract, version 1 (docs/integration-contracts.md §3).

Every adapter must produce records that validate against ``SourceRecord``. Unknown fields never
reach the analytical store: the pipeline processes only the validated, normalised record
(``model_dump``), counts the dropped fields in the batch audit event, and keeps the payload exactly as
received only in the immutable raw-staging table, which the API cannot read (data minimisation).
Versions and flags are strict: a value that would need guessing (``"2"``, ``2.0``, ``True`` as a
version, ``1`` as a flag) fails validation and goes to the dead-letter queue.
"""

from typing import Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    model_validator,
)

RecordType = Literal["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT", "CIVIC_CONTEXT"]
Classification = Literal["INTERNAL", "RESTRICTED", "HIGHLY_RESTRICTED"]
Precision = Literal["EXACT", "APPROXIMATE", "GRID", "UNKNOWN"]
GeocodeMethod = Literal["SOURCE_COORDINATE", "LOCALITY_CENTROID", "MANUAL", "SUGGESTED", "NONE"]

Code = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
Text = Field(default=None, max_length=300)


class SourceLocation(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    text: str | None = Text
    locality: str | None = Text
    precision: Precision
    method: GeocodeMethod
    confidence: float | None = Field(default=None, ge=0, le=1)
    is_residential: StrictBool = False

    @model_validator(mode="after")
    def _both_or_neither(self) -> Self:
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class RestrictedDetail(BaseModel):
    """Routed only to the restricted schema. Demo values are opaque synthetic tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    victim_reference_token: str | None = Field(default=None, max_length=64)
    witness_reference_token: str | None = Field(default=None, max_length=64)


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    source_system: str = Code
    source_record_reference: str = Field(min_length=1, max_length=100)
    source_version: StrictInt = Field(ge=1, le=2**31 - 1)
    record_type: RecordType
    official_offence_act: str | None = Text
    official_offence_section: str | None = Field(default=None, max_length=64)
    official_offence_name: str | None = Text
    event_type_code: str | None = Field(default=None, max_length=64)
    occurrence_start_at: AwareDatetime | None = None
    occurrence_end_at: AwareDatetime | None = None
    reported_at: AwareDatetime
    reporting_station_code: str = Code
    status: str | None = Field(default=None, max_length=64)
    location: SourceLocation | None = None
    sensitivity_classification: Classification
    restricted: RestrictedDetail | None = None

    _dropped: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="wrap")
    @classmethod
    def _record_dropped_fields(cls, data: Any, handler: Any) -> Any:
        dropped = sorted(set(data) - set(cls.model_fields)) if isinstance(data, dict) else []
        model = handler(data)
        model._dropped = dropped
        return model

    @model_validator(mode="after")
    def _consistency(self) -> Self:
        if self.record_type == "CASE" and not self.official_offence_section:
            raise ValueError("CASE records require official_offence_section")
        if (
            self.occurrence_start_at is not None
            and self.occurrence_end_at is not None
            and self.occurrence_end_at < self.occurrence_start_at
        ):
            raise ValueError("occurrence_end_at is before occurrence_start_at")
        return self

    @property
    def dropped_fields(self) -> list[str]:
        return list(self._dropped)

    @property
    def classification_code(self) -> str | None:
        """Code used for the analytical category mapping (offence section or event type)."""
        return self.official_offence_section or self.event_type_code
