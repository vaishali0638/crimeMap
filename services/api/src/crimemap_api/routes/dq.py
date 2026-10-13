"""Data-quality review queue (``dq.manage``).

Three independent filters decide what a caller sees (ADR-028): row-level security (the union of the
caller's grants; issues about records the caller cannot see, such as HIGHLY_RESTRICTED incidents,
are invisible), an explicit filter to the territory of the grants that confer ``dq.manage``
(``permission_scope``), and field-level minimisation. The queue shows the data-quality facts a
steward needs (precision, confidence, method, times) but never coordinates. It shows analytical
categories only to callers who also hold ``incident.sensitive_category.read``: hiding the category
for *every* incident (rather than flagging the sensitive ones) is what keeps sensitive cases
indistinguishable. Rule details are whitelisted per rule, and a possible duplicate's reference is
shown only if the caller may see that record. An out-of-scope region filter is refused (403,
audited), not silently narrowed.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.deps import Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.engine import PermissionScope, Target, permission_scope

router = APIRouter(prefix="/api/v1/dq", tags=["data-quality"])

DqCtx = Annotated[Ctx, Depends(Requirement("dq.manage"))]

State = Literal["OPEN", "ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"]
IssueType = Literal[
    "MISSING_LOCATION",
    "LOW_CONFIDENCE_LOCATION",
    "MISSING_OCCURRENCE_TIME",
    "POSSIBLE_DUPLICATE",
    "INVALID_CATEGORY_MAPPING",
    "STALE_SOURCE",
    "OUTSIDE_JURISDICTION",
    "OTHER",
]
Severity = Literal["LOW", "MEDIUM", "HIGH"]

TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"ASSIGNED", "RESOLVED", "ACCEPTED_AS_IS"},
    "ASSIGNED": {"OPEN", "RESOLVED", "ACCEPTED_AS_IS"},
    "RESOLVED": {"OPEN"},
    "ACCEPTED_AS_IS": {"OPEN"},
}

# Detail keys each rule may expose (anything else the worker stored is withheld).
DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "DQ-LOC-002": ("confidence",),
    "DQ-CAT-001": ("code",),
    "DQ-REF-001": ("reason",),
    "DQ-DUP-001": ("distance_m",),
    "DQ-SRC-001": ("last_extracted_at", "stale_after_hours"),
    "DQ-REC-001": ("reason", "day", "unexplained"),
}


class IncidentFacts(BaseModel):
    """Data-quality view of an incident: no coordinates, no restricted fields."""

    reference: str
    record_type: str
    # null unless the caller holds incident.sensitive_category.read (then shown for every incident)
    category_code: str | None
    occurrence_start_at: datetime | None
    reported_at: datetime | None
    quality_status: str
    location_precision: str | None
    location_confidence: float | None
    geocode_method: str | None


class SourceFacts(BaseModel):
    code: str
    name: str


class DuplicatePartner(BaseModel):
    """The other record of a possible duplicate: its reference only if the caller may see it."""

    visible: bool
    reference: str | None


class IssueOut(BaseModel):
    id: UUID
    subject_type: str
    issue_type: str
    severity: str
    rule_code: str
    state: str
    detected_by: str
    region_code: str | None
    station_code: str | None
    assigned_to: UUID | None
    due_at: datetime | None
    resolution_note: str | None
    detail: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    incident: IncidentFacts | None
    duplicate_of: DuplicatePartner | None
    source: SourceFacts | None


class CursorPage(BaseModel):
    next_cursor: str | None
    has_more: bool


class IssuePage(BaseModel):
    data: list[IssueOut]
    pagination: CursorPage
    categories_shown: bool


class TypeCount(BaseModel):
    issue_type: str
    severity: str
    count: int


class SummaryOut(BaseModel):
    total: int
    by_state: dict[str, int]
    by_type: list[TypeCount]


class IssueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    state: State
    resolution_note: str | None = Field(default=None, min_length=5, max_length=2000)

    @model_validator(mode="after")
    def _note_for_closure(self) -> Self:
        if self.state in ("RESOLVED", "ACCEPTED_AS_IS") and not self.resolution_note:
            raise ValueError("a resolution note is required to resolve or accept an issue")
        return self


# Statements are written out in full (no string composition) so static analysis sees literal SQL.
# Every list and update applies the same scope predicate: the territory of the caller's dq.manage
# grants, plus source-level issues, which carry no territory. RLS additionally hides issues about
# records the caller cannot see; the duplicate partner join is RLS-filtered too.
_LIST = text(
    """
SELECT q.id, q.subject_type, q.issue_type, q.severity, q.rule_code, q.state, q.detected_by,
       q.region_id, q.station_id, r.code AS region_code, st.code AS station_code, q.assigned_to,
       q.due_at, q.resolution_note, q.detail, q.created_at, q.updated_at, q.incident_id,
       i.id AS visible_incident_id, i.source_record_reference, i.record_type,
       c.code AS category_code, i.occurrence_start_at, i.reported_at,
       i.record_quality_status, l.precision_level, l.confidence_score, l.geocode_method,
       s.code AS source_code, s.name AS source_name,
       dup.source_record_reference AS dup_reference, dup.region_id AS dup_region_id,
       dup.station_id AS dup_station_id
FROM core.data_quality_issue q
LEFT JOIN core.organizational_unit r ON r.id = q.region_id
LEFT JOIN core.organizational_unit st ON st.id = q.station_id
LEFT JOIN core.incident_record i ON i.id = q.incident_id
LEFT JOIN core.analytical_category c ON c.id = i.analytical_category_id
LEFT JOIN core.incident_location l ON l.incident_id = i.id
LEFT JOIN core.source_system s ON q.subject_type = 'SOURCE_SYSTEM' AND s.id = q.subject_id
LEFT JOIN core.incident_record dup ON q.rule_code = 'DQ-DUP-001'
      AND dup.id = CAST(q.detail->>'duplicate_of_id' AS uuid)
WHERE (:global OR q.region_id = ANY(:regions) OR q.station_id = ANY(:stations)
       OR (q.subject_type = 'SOURCE_SYSTEM' AND q.region_id IS NULL AND q.station_id IS NULL))
  AND (CAST(:state AS text) IS NULL OR q.state = :state)
  AND (CAST(:issue_type AS text) IS NULL OR q.issue_type = :issue_type)
  AND (CAST(:severity AS text) IS NULL OR q.severity = :severity)
  AND (CAST(:subject_type AS text) IS NULL OR q.subject_type = :subject_type)
  AND (CAST(:region AS uuid) IS NULL OR q.region_id = :region)
  AND (NOT :mine OR q.assigned_to = :user_id)
  AND (CAST(:after_t AS timestamptz) IS NULL
       OR (q.created_at, q.id) < (CAST(:after_t AS timestamptz), CAST(:after_id AS uuid)))
ORDER BY q.created_at DESC, q.id DESC LIMIT :limit
"""
)

_SUMMARY = text(
    """
SELECT q.state, q.issue_type, q.severity, count(*) AS n FROM core.data_quality_issue q
WHERE (:global OR q.region_id = ANY(:regions) OR q.station_id = ANY(:stations)
       OR (q.subject_type = 'SOURCE_SYSTEM' AND q.region_id IS NULL AND q.station_id IS NULL))
  AND (CAST(:state AS text) IS NULL OR q.state = :state)
GROUP BY q.state, q.issue_type, q.severity
"""
)

_LOAD_FOR_UPDATE = text(
    """
SELECT q.id, q.subject_type, q.issue_type, q.severity, q.rule_code, q.state, q.detected_by,
       q.region_id, q.station_id, r.code AS region_code, st.code AS station_code, q.assigned_to,
       q.due_at, q.resolution_note, q.detail, q.created_at, q.updated_at, q.incident_id,
       i.id AS visible_incident_id, i.source_record_reference, i.record_type,
       c.code AS category_code, i.occurrence_start_at, i.reported_at,
       i.record_quality_status, l.precision_level, l.confidence_score, l.geocode_method,
       s.code AS source_code, s.name AS source_name,
       dup.source_record_reference AS dup_reference, dup.region_id AS dup_region_id,
       dup.station_id AS dup_station_id
FROM core.data_quality_issue q
LEFT JOIN core.organizational_unit r ON r.id = q.region_id
LEFT JOIN core.organizational_unit st ON st.id = q.station_id
LEFT JOIN core.incident_record i ON i.id = q.incident_id
LEFT JOIN core.analytical_category c ON c.id = i.analytical_category_id
LEFT JOIN core.incident_location l ON l.incident_id = i.id
LEFT JOIN core.source_system s ON q.subject_type = 'SOURCE_SYSTEM' AND s.id = q.subject_id
LEFT JOIN core.incident_record dup ON q.rule_code = 'DQ-DUP-001'
      AND dup.id = CAST(q.detail->>'duplicate_of_id' AS uuid)
WHERE q.id = :id
  AND (:global OR q.region_id = ANY(:regions) OR q.station_id = ANY(:stations)
       OR (q.subject_type = 'SOURCE_SYSTEM' AND q.region_id IS NULL AND q.station_id IS NULL))
FOR UPDATE OF q
"""
)

_REGION_ID = text(
    "SELECT id FROM core.organizational_unit WHERE unit_type = 'REGION' AND code = :c"
)


def _scope_params(scope: PermissionScope) -> dict[str, Any]:
    return {
        "global": scope.is_global,
        "regions": list(scope.region_ids),
        "stations": list(scope.station_ids),
    }


def _in_scope(scope: PermissionScope, region_id: Any, station_id: Any) -> bool:
    return (
        scope.is_global
        or (region_id is not None and region_id in scope.region_ids)
        or (station_id is not None and station_id in scope.station_ids)
    )


def _encode_cursor(created_at: datetime, issue_id: UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(issue_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """A cursor only positions the keyset (the scope predicate is always applied); anything
    malformed is a 400, never a 500."""
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not (
            isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value)
        ):
            raise ValueError("cursor shape")
        stamp = datetime.fromisoformat(value[0])
        if stamp.tzinfo is None:
            raise ValueError("naive cursor timestamp")
        return stamp, UUID(value[1])
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError):
        raise ProblemError(400, "invalid-cursor", "Invalid pagination cursor") from None


def _issue(row: Any, *, scope: PermissionScope, categories_shown: bool) -> IssueOut:
    incident = None
    if row.visible_incident_id is not None:
        incident = IncidentFacts(
            reference=row.source_record_reference,
            record_type=row.record_type,
            category_code=row.category_code if categories_shown else None,
            occurrence_start_at=row.occurrence_start_at,
            reported_at=row.reported_at,
            quality_status=row.record_quality_status,
            location_precision=row.precision_level,
            location_confidence=(
                float(row.confidence_score) if row.confidence_score is not None else None
            ),
            geocode_method=row.geocode_method,
        )
    duplicate_of = None
    if row.rule_code == "DQ-DUP-001":
        visible = row.dup_reference is not None and _in_scope(
            scope, row.dup_region_id, row.dup_station_id
        )
        duplicate_of = DuplicatePartner(
            visible=visible, reference=row.dup_reference if visible else None
        )
    raw_detail = row.detail or {}
    return IssueOut(
        id=row.id,
        subject_type=row.subject_type,
        issue_type=row.issue_type,
        severity=row.severity,
        rule_code=row.rule_code,
        state=row.state,
        detected_by=row.detected_by,
        region_code=row.region_code,
        station_code=row.station_code,
        assigned_to=row.assigned_to,
        due_at=row.due_at,
        resolution_note=row.resolution_note,
        detail={
            k: raw_detail[k]
            for k in DETAIL_KEYS.get(
                row.rule_code, ("reason",) if row.rule_code.startswith("USER-") else ()
            )
            if k in raw_detail
        },
        created_at=row.created_at,
        updated_at=row.updated_at,
        incident=incident,
        duplicate_of=duplicate_of,
        source=SourceFacts(code=row.source_code, name=row.source_name) if row.source_code else None,
    )


def _categories_shown(ctx: Ctx, scope: PermissionScope) -> bool:
    """Categories are shown only if the caller may read sensitive categories wherever it may
    manage data quality (otherwise a missing category would single out the sensitive cases)."""
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    if sensitive.is_global:
        return True
    if scope.is_global:
        return False
    return scope.region_ids <= sensitive.region_ids and scope.station_ids <= (sensitive.station_ids)


@router.get("/issues")
async def list_issues(
    ctx: DqCtx,
    state: State | None = None,
    issue_type: IssueType | None = None,
    severity: Severity | None = None,
    subject_type: Literal["INCIDENT", "SOURCE_SYSTEM"] | None = None,
    region: Annotated[str | None, Query(pattern=r"^[A-Z]{2,5}$")] = None,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> IssuePage:
    after = _decode_cursor(cursor) if cursor else None
    scope = permission_scope(ctx.subject, "dq.manage")
    region_id = None
    if region is not None:
        async with ctx.services.sessions() as session:
            region_id = (await session.execute(_REGION_ID, {"c": region})).scalar_one_or_none()
        # An out-of-scope (or unknown) region filter is refused and audited, not silently narrowed.
        ctx.require("dq.manage", target=Target(region_id=region_id))
    filters = {
        "state": state,
        "issue_type": issue_type,
        "severity": severity,
        "subject_type": subject_type,
        "region": region_id,
        "mine": mine,
    }
    shown = _categories_shown(ctx, scope)
    async with ctx.uow() as uow:
        rows = (
            await uow.session.execute(
                _LIST,
                {
                    **_scope_params(scope),
                    **filters,
                    "user_id": ctx.user.id,
                    "after_t": after[0] if after else None,
                    "after_id": after[1] if after else None,
                    "limit": limit + 1,
                },
            )
        ).all()
        page, has_more = rows[:limit], len(rows) > limit
        audited_filters = {k: v for k, v in filters.items() if v not in (None, False)}
        if region is not None:
            audited_filters["region"] = region
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="dq_issue",
                filters=audited_filters | {"limit": limit, "paged": cursor is not None},
                after_meta={"count": len(page), "issue_ids": [str(r.id) for r in page]},
            )
        )
        await uow.commit()
    return IssuePage(
        data=[_issue(r, scope=scope, categories_shown=shown) for r in page],
        pagination=CursorPage(
            next_cursor=_encode_cursor(page[-1].created_at, page[-1].id) if has_more else None,
            has_more=has_more,
        ),
        categories_shown=shown,
    )


@router.get("/summary")
async def summary(ctx: DqCtx, state: State | None = None) -> SummaryOut:
    scope = permission_scope(ctx.subject, "dq.manage")
    async with ctx.uow() as uow:
        rows = (await uow.session.execute(_SUMMARY, {**_scope_params(scope), "state": state})).all()
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="dq_summary",
                filters={"state": state} if state else None,
                after_meta={"groups": len(rows)},
            )
        )
        await uow.commit()
    by_state: dict[str, int] = {}
    by_type: dict[tuple[str, str], int] = {}
    for r in rows:
        by_state[r.state] = by_state.get(r.state, 0) + r.n
        by_type[(r.issue_type, r.severity)] = by_type.get((r.issue_type, r.severity), 0) + r.n
    return SummaryOut(
        total=sum(by_state.values()),
        by_state=by_state,
        by_type=[
            TypeCount(issue_type=t, severity=s, count=n) for (t, s), n in sorted(by_type.items())
        ],
    )


async def _load_for_update(session: AsyncSession, issue_id: UUID, scope: PermissionScope) -> Any:
    return (
        await session.execute(_LOAD_FOR_UPDATE, {"id": issue_id, **_scope_params(scope)})
    ).one_or_none()


@router.patch("/issues/{issue_id}")
async def update_issue(ctx: DqCtx, issue_id: UUID, body: IssueUpdate) -> IssueOut:
    scope = permission_scope(ctx.subject, "dq.manage")
    event_base: dict[str, Any] = {"object_type": "dq_issue", "object_id": str(issue_id)}
    async with ctx.uow() as uow:
        row = await _load_for_update(uow.session, issue_id, scope)
        if row is None or (row.incident_id is not None and row.visible_incident_id is None):
            await uow.session.rollback()
            raise await ctx.deny(
                ctx.event("DQ_ISSUE_UPDATE", "DENY", reason="NOT_FOUND_IN_SCOPE", **event_base),
                404,
                "not-found",
                "Not found",
            )
        territorial = row.subject_type != "SOURCE_SYSTEM"
        ctx.require(
            "dq.manage",
            target=Target(region_id=row.region_id, station_id=row.station_id)
            if territorial
            else None,
        )
        if body.state not in TRANSITIONS[row.state]:
            await uow.session.rollback()
            raise await ctx.deny(
                ctx.event(
                    "DQ_ISSUE_UPDATE",
                    "DENY",
                    reason="INVALID_TRANSITION",
                    before_meta={"state": row.state},
                    after_meta={"requested_state": body.state},
                    **event_base,
                ),
                409,
                "invalid-state-transition",
                f"An issue in state {row.state} cannot become {body.state}",
            )
        assigned_to = (
            ctx.user.id
            if body.state == "ASSIGNED"
            else None
            if body.state == "OPEN"
            else row.assigned_to
        )
        await uow.session.execute(
            text(
                "UPDATE core.data_quality_issue SET state = :state, assigned_to = :assigned, "
                "resolution_note = :note, updated_at = now() WHERE id = :id"
            ),
            {
                "state": body.state,
                "assigned": assigned_to,
                "note": body.resolution_note if body.state != "OPEN" else None,
                "id": issue_id,
            },
        )
        uow.audit(
            ctx.event(
                "DQ_ISSUE_UPDATE",
                before_meta={
                    "state": row.state,
                    "assigned_to": str(row.assigned_to) if row.assigned_to else None,
                },
                after_meta={
                    "state": body.state,
                    "assigned_to": str(assigned_to) if assigned_to else None,
                    "issue_type": row.issue_type,
                    "note_length": len(body.resolution_note or ""),
                },
                **event_base,
            )
        )
        updated = await _load_for_update(uow.session, issue_id, scope)
        await uow.commit()
    return _issue(updated, scope=scope, categories_shown=_categories_shown(ctx, scope))
