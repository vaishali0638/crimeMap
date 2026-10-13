"""Incident popup summary, permitted details, and user data-issue flags (Phase 3).

There is deliberately **no** route that creates or changes an official case record (HM-5): case
records come only from approved source systems through ingestion. The one write here creates a
data-quality issue *about* a record.

Field masking follows data-classification §6: summaries never include coordinates, identity
references or narrative; station officers see times to the hour; sensitive-category records are
withheld (404) from callers without ``incident.sensitive_category.read`` for that territory, because
those categories are aggregate-only for them. A record outside the caller's scope is a 404 (its
existence is not disclosed) and the attempt is audited.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from crimemap_api.deps import Ctx, Requirement
from crimemap_api.policy.catalogue import Role
from crimemap_api.policy.engine import Target, authorize

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

SummaryCtx = Annotated[Ctx, Depends(Requirement("incident.summary.read"))]
DetailCtx = Annotated[Ctx, Depends(Requirement("incident.detail.read"))]
FlagCtx = Annotated[Ctx, Depends(Requirement("opsrecord.create"))]

# Roles whose case-level view includes full times (data-classification §6).
MINUTE_ROLES = {
    Role.SHO,
    Role.DISTRICT_OR_REGION_SUPERVISOR,
    Role.CRIME_ANALYST,
    Role.DATA_STEWARD,
}

_LOAD = text(
    """
    SELECT i.id, i.source_record_reference, i.source_version, i.record_type, i.region_id, i.station_id,
           i.is_sensitive_category, i.official_offence_act, i.official_offence_section,
           i.official_offence_name, i.occurrence_start_at, i.occurrence_end_at, i.reported_at,
           i.status_code, i.record_quality_status, c.code AS category_code, c.name AS category_label,
           r.name AS region_name, st.name AS station_name, bt.name AS beat_name, rs.name AS reporting_station,
           l.locality, l.precision_level, l.confidence_score, l.geocode_method,
           sd.label AS status_label, ss.code AS source_code, ss.name AS source_name
    FROM core.incident_record i
    LEFT JOIN core.analytical_category c ON c.id = i.analytical_category_id
    LEFT JOIN core.incident_location l ON l.incident_id = i.id
    LEFT JOIN core.organizational_unit r ON r.id = i.region_id
    LEFT JOIN core.organizational_unit st ON st.id = i.station_id
    LEFT JOIN core.organizational_unit bt ON bt.id = i.beat_id
    LEFT JOIN core.organizational_unit rs ON rs.id = i.reporting_station_id
    LEFT JOIN core.status_definition sd ON sd.record_type = i.record_type AND sd.code = i.status_code
    JOIN core.source_system ss ON ss.id = i.source_system_id
    WHERE i.id = :id
    """
)


class IncidentSummaryOut(BaseModel):
    id: UUID
    reference: str
    category_code: str | None
    category_label: str | None
    offence_section: str | None  # CASE records only
    offence_name: str | None
    event_type: str | None  # ERSS calls, accidents, traffic events (source event code)
    occurred_from: datetime | None
    occurred_to: datetime | None
    reported_at: datetime | None
    locality: str | None
    location_precision: str | None
    location_confidence: float | None
    station: str | None
    region: str | None
    status: str | None
    time_precision: Literal["MINUTE", "HOUR"]
    source_system: str


class IssueBrief(BaseModel):
    issue_type: str
    severity: str
    state: str


class DetailOut(IncidentSummaryOut):
    record_type: str
    offence_act: str | None
    beat: str | None
    reporting_station: str | None
    quality_status: str
    geocode_method: str | None
    source_version: int
    source_received_at: datetime | None
    open_issues: list[IssueBrief]
    notes: int


IST = ZoneInfo("Asia/Kolkata")


def _hour(value: datetime | None) -> datetime | None:
    """Truncate to the hour as officers read it (IST is UTC+05:30, so a UTC hour would be :30)."""
    return value.astimezone(IST).replace(minute=0, second=0, microsecond=0) if value else None


def _minute_precision(ctx: Ctx, target: Target) -> bool:
    """Full times if any non-officer case-level role covers the record."""
    for g in ctx.subject.grants:
        if g.role in MINUTE_ROLES:
            single = type(ctx.subject)(
                user_id=ctx.subject.user_id,
                grants=(g,),
                mfa=ctx.subject.mfa,
                auth_time=ctx.subject.auth_time,
            )
            if authorize(single, "incident.summary.read", target=target).allowed:
                return True
    return False


async def _load_visible(ctx: Ctx, incident_id: UUID, permission: str) -> tuple[Any, Target]:
    """The record if the caller may see it under ``permission``; otherwise an audited 404."""
    async with ctx.uow() as uow:
        row = (await uow.session.execute(_LOAD, {"id": incident_id})).one_or_none()
        await uow.commit()
    target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
    visible = (
        row is not None
        and authorize(ctx.subject, permission, target=target).allowed
        and (
            not row.is_sensitive_category
            or authorize(ctx.subject, "incident.sensitive_category.read", target=target).allowed
        )
    )
    if not visible:
        raise await ctx.deny(
            ctx.event(
                "RECORD_VIEW",
                "DENY",
                reason="NOT_FOUND_IN_SCOPE",
                object_type="incident",
                object_id=str(incident_id),
            ),
            404,
            "not-found",
            "Not found",
        )
    return row, target  # type: ignore[return-value]  # visible implies a target


def _summary(row: Any, *, minute: bool) -> dict[str, Any]:
    clip = (lambda v: v) if minute else _hour
    return {
        "id": row.id,
        "reference": row.source_record_reference,
        "category_code": row.category_code,
        "category_label": row.category_label,
        # Ingestion stores a non-case record's event code in the offence-section column; present it
        # as what it is (found in Phase 3 browser testing).
        "offence_section": row.official_offence_section if row.record_type == "CASE" else None,
        "offence_name": row.official_offence_name if row.record_type == "CASE" else None,
        "event_type": None if row.record_type == "CASE" else row.official_offence_section,
        "occurred_from": clip(row.occurrence_start_at),
        "occurred_to": clip(row.occurrence_end_at),
        "reported_at": clip(row.reported_at),
        "locality": row.locality,
        "location_precision": row.precision_level,
        "location_confidence": float(row.confidence_score)
        if row.confidence_score is not None
        else None,
        "station": row.station_name,
        "region": row.region_name,
        "status": row.status_label or row.status_code,
        "time_precision": "MINUTE" if minute else "HOUR",
        "source_system": row.source_name,
    }


@router.get("/{incident_id}/summary")
async def incident_summary(ctx: SummaryCtx, incident_id: UUID) -> IncidentSummaryOut:
    row, target = await _load_visible(ctx, incident_id, "incident.summary.read")
    minute = _minute_precision(ctx, target)
    async with ctx.uow() as uow:
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="incident_summary", object_id=str(incident_id))
        )
        await uow.commit()
    return IncidentSummaryOut(**_summary(row, minute=minute))


@router.get("/{incident_id}")
async def incident_details(ctx: DetailCtx, incident_id: UUID) -> DetailOut:
    row, target = await _load_visible(ctx, incident_id, "incident.detail.read")
    minute = _minute_precision(ctx, target)
    async with ctx.uow() as uow:
        issues = (
            await uow.session.execute(
                text(
                    "SELECT issue_type, severity, state FROM core.data_quality_issue "
                    "WHERE incident_id = :id AND state IN ('OPEN', 'ASSIGNED') ORDER BY severity, issue_type"
                ),
                {"id": incident_id},
            )
        ).all()
        received = (
            await uow.session.execute(
                text(
                    "SELECT max(received_at) FROM core.source_record_link WHERE incident_id = :id"
                ),
                {"id": incident_id},
            )
        ).scalar_one_or_none()
        notes = (
            await uow.session.execute(
                text(
                    "SELECT count(*) FROM core.analyst_note WHERE subject_type = 'INCIDENT' AND subject_id = :id"
                ),
                {"id": incident_id},
            )
        ).scalar_one()
        uow.audit(ctx.event("RECORD_VIEW", object_type="incident", object_id=str(incident_id)))
        await uow.commit()
    return DetailOut(
        **_summary(row, minute=minute),
        record_type=row.record_type,
        offence_act=row.official_offence_act,
        beat=row.beat_name,
        reporting_station=row.reporting_station,
        quality_status=row.record_quality_status,
        geocode_method=row.geocode_method,
        source_version=row.source_version,
        source_received_at=received,
        open_issues=[
            IssueBrief(issue_type=i.issue_type, severity=i.severity, state=i.state) for i in issues
        ],
        notes=notes,
    )


class FlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    issue_type: Literal[
        "MISSING_LOCATION",
        "LOW_CONFIDENCE_LOCATION",
        "MISSING_OCCURRENCE_TIME",
        "POSSIBLE_DUPLICATE",
        "INVALID_CATEGORY_MAPPING",
        "OUTSIDE_JURISDICTION",
        "OTHER",
    ]
    note: str = Field(min_length=5, max_length=500)


class FlagOut(BaseModel):
    id: UUID
    issue_type: str
    state: str


@router.post("/{incident_id}/data-issues", status_code=201, response_model=FlagOut)
async def flag_data_issue(ctx: FlagCtx, incident_id: UUID, body: FlagIn) -> Any:
    """'Flag data issue' from the popup: a USER-detected issue for the data steward's queue."""
    row, target = await _load_visible(ctx, incident_id, "incident.summary.read")
    ctx.require("opsrecord.create", target=target)
    async with ctx.uow() as uow:
        issue_id = (
            await uow.session.execute(
                text(
                    "INSERT INTO core.data_quality_issue (incident_id, subject_type, subject_id, issue_type, "
                    "severity, detected_by, rule_code, detail, region_id, station_id, due_at) "
                    "VALUES (:id, 'INCIDENT', :id, :type, 'MEDIUM', 'USER', :rule, "
                    "jsonb_build_object('reason', CAST(:note AS text)), :region, :station, now() + interval '7 days') "
                    "ON CONFLICT (subject_type, subject_id, rule_code) DO NOTHING RETURNING id"
                ),
                {
                    "id": incident_id,
                    "type": body.issue_type,
                    "rule": f"USER-{body.issue_type}",
                    "note": body.note,
                    "region": row.region_id,
                    "station": row.station_id,
                },
            )
        ).scalar_one_or_none()
        reopened = False
        if issue_id is None:
            # The flag exists. If it was resolved or accepted, flagging again reopens it for the
            # steward (the original reason is kept; only state columns are writable here).
            issue_id = (
                await uow.session.execute(
                    text(
                        "UPDATE core.data_quality_issue SET state = 'OPEN', assigned_to = NULL, "
                        "resolution_note = NULL, updated_at = now() "
                        "WHERE subject_type = 'INCIDENT' AND subject_id = :id AND rule_code = :rule "
                        "AND state IN ('RESOLVED', 'ACCEPTED_AS_IS') RETURNING id"
                    ),
                    {"id": incident_id, "rule": f"USER-{body.issue_type}"},
                )
            ).scalar_one_or_none()
            reopened = issue_id is not None
        if issue_id is None:
            await uow.session.rollback()
            raise await ctx.deny(
                ctx.event(
                    "DQ_ISSUE_FLAG",
                    "DENY",
                    reason="ALREADY_FLAGGED",
                    object_type="incident",
                    object_id=str(incident_id),
                ),
                409,
                "invalid-state-transition",
                "This issue type has already been flagged for this record",
            )
        uow.audit(
            ctx.event(
                "DQ_ISSUE_FLAG",
                object_type="dq_issue",
                object_id=str(issue_id),
                after_meta={
                    "incident_id": str(incident_id),
                    "issue_type": body.issue_type,
                    "note_length": len(body.note),
                    "reopened": reopened,
                },
            )
        )
        await uow.commit()
    return JSONResponse(
        status_code=201,
        content=FlagOut(id=issue_id, issue_type=body.issue_type, state="OPEN").model_dump(
            mode="json"
        ),
    )
