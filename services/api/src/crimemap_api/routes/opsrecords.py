"""'+ Add Operational Record', review of records that need it, analyst notes, and the map layer of
approved event markers and observations (Phase 3).

An operational record is supplementary information entered in CRIMEMAP. It is **never** an FIR or
case record: there is no such kind, and nothing here writes to ``core.incident_record`` (HM-4, HM-5).

Kinds and review:
- ANALYST_NOTE, OPERATIONAL_OBSERVATION: recorded directly (review NOT_REQUIRED).
- LOCATION_CORRECTION_REQUEST (needs the record and a corrected point), APPROVED_EVENT_MARKER:
  PENDING until a reviewer with ``opsrecord.approve`` in that territory decides. The reviewer must be
  a different person (also enforced by a CHECK constraint). A DATA_STEWARD's approve grant covers
  location corrections only ("corrections" in the role matrix). Approving a correction records the
  decision; applying it to the analytical location is not built yet (a correction workflow).
- CIVIC_CONTEXT_REFERENCE: refused while the ``civic_context`` feature flag is off (default).
- ACTION_COMPLETION: refused here. Completion is recorded on the approved operational action itself
  (``POST /actions/{id}/completions``, Review Queue), where approval and assignment are enforced.
Free text (title, reason, note) is stored as text and never written to the audit log (lengths only).

Visibility (audit fixes): a record that refers to an incident is shown only to callers who may see
that incident (territory, HIGHLY_RESTRICTED via row-level security, sensitive category); a record's
point is exact only for its author and within the scope of ``incident.location.exact``, otherwise it
is the centre of its 250 m cell, as for incidents (ADR-020).
"""

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from crimemap_api.deps import AUTHENTICATED, Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.catalogue import ROLE_PERMISSIONS, Role
from crimemap_api.policy.engine import PermissionScope, Target, authorize, permission_scope
from crimemap_api.territory import load_region, refuse, region_allowed

router = APIRouter(prefix="/api/v1", tags=["operational-records"])

CreateCtx = Annotated[Ctx, Depends(Requirement("opsrecord.create"))]
ApproveCtx = Annotated[Ctx, Depends(Requirement("opsrecord.approve"))]
PointsCtx = Annotated[Ctx, Depends(Requirement("map.points.read"))]
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]

Kind = Literal[
    "ANALYST_NOTE",
    "LOCATION_CORRECTION_REQUEST",
    "APPROVED_EVENT_MARKER",
    "OPERATIONAL_OBSERVATION",
    "ACTION_COMPLETION",
    "CIVIC_CONTEXT_REFERENCE",
]
IST = ZoneInfo("Asia/Kolkata")
NEEDS_REVIEW = {"LOCATION_CORRECTION_REQUEST", "APPROVED_EVENT_MARKER", "CIVIC_CONTEXT_REFERENCE"}


class PointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Kind
    title: str = Field(min_length=3, max_length=200)
    occurred_at: AwareDatetime
    location: PointIn | None = None
    locality: str | None = Field(default=None, max_length=200)
    region: str = Field(pattern=r"^[A-Z]{2,5}$")
    station: str | None = Field(default=None, pattern=r"^[A-Z0-9-]{2,20}$")
    precision: Literal["EXACT", "APPROXIMATE", "GRID", "UNKNOWN"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=3, max_length=2000)
    evidence_reference: str | None = Field(default=None, max_length=500)
    visibility: Literal["INTERNAL", "RESTRICTED"]
    subject_incident_id: UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.location is None and not self.locality:
            raise ValueError("give a location (map point) or a locality")
        if self.occurred_at > datetime.now(self.occurred_at.tzinfo) + timedelta(hours=1):
            raise ValueError("occurred_at is in the future")
        if self.kind == "LOCATION_CORRECTION_REQUEST" and (
            self.subject_incident_id is None or self.location is None
        ):
            raise ValueError("a location correction needs the record and the corrected map point")
        return self


class RecordOut(BaseModel):
    id: UUID
    kind: str
    title: str
    occurred_at: datetime
    region: str
    station: str | None
    beat: str | None
    locality: str | None
    location: PointIn | None
    precision: str
    confidence: float
    reason: str
    evidence_reference: str | None
    visibility: str
    subject_incident_id: UUID | None
    review_status: str
    review_note: str | None
    created_at: datetime
    created_by_me: bool


class RecordList(BaseModel):
    data: list[RecordOut]


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["APPROVE", "REJECT"]
    note: str = Field(min_length=3, max_length=2000)


# The subject incident is joined under row-level security (absent if HIGHLY_RESTRICTED or out of
# territory); `snapped` is the 250 m cell centre in the region's metric CRS.
_SELECT = text(
    """
    SELECT o.id, o.record_kind, o.title, o.occurred_at, r.code AS region, st.code AS station, b.code AS beat,
           o.locality, ST_X(o.geometry) AS lon, ST_Y(o.geometry) AS lat,
           ST_X(sn.p) AS slon, ST_Y(sn.p) AS slat, o.precision_level, o.confidence,
           o.reason, o.evidence_reference, o.visibility_classification, o.subject_incident_id,
           o.review_status, o.review_note, o.created_at, o.created_by, o.region_id, o.station_id,
           i.id IS NOT NULL AS subject_seen, i.region_id AS subject_region_id,
           i.station_id AS subject_station_id, coalesce(c.is_sensitive, false) AS subject_sensitive
    FROM core.operational_record o
    JOIN core.organizational_unit r ON r.id = o.region_id
    LEFT JOIN core.organizational_unit st ON st.id = o.station_id
    LEFT JOIN core.organizational_unit b ON b.id = o.beat_id
    LEFT JOIN core.incident_record i ON i.id = o.subject_incident_id
    LEFT JOIN core.analytical_category c ON c.id = i.analytical_category_id
    LEFT JOIN LATERAL (
        SELECT ST_Transform(ST_SetSRID(ST_MakePoint(floor(ST_X(t.m) / 250) * 250 + 125,
                                                    floor(ST_Y(t.m) / 250) * 250 + 125), r.analysis_crs), 4326) AS p
        FROM (SELECT ST_Transform(o.geometry, r.analysis_crs) AS m) t WHERE o.geometry IS NOT NULL
    ) sn ON true
    WHERE (CAST(:id AS uuid) IS NULL OR o.id = :id)
      AND (CAST(:status AS text) IS NULL OR o.review_status = :status)
      AND (CAST(:kind AS text) IS NULL OR o.record_kind = :kind)
      AND (:g OR o.region_id = ANY (CAST(:r AS uuid[])) OR o.station_id = ANY (CAST(:s AS uuid[])))
    ORDER BY o.created_at DESC
    LIMIT :limit
    """
)


def _subject_visible(ctx: Ctx, row: Any) -> bool:
    """May the caller see the incident this record refers to (if any)?"""
    if row.subject_incident_id is None:
        return True
    if not row.subject_seen:
        return False
    target = Target(region_id=row.subject_region_id, station_id=row.subject_station_id)
    return authorize(ctx.subject, "incident.summary.read", target=target).allowed and (
        not row.subject_sensitive
        or authorize(ctx.subject, "incident.sensitive_category.read", target=target).allowed
    )


def _exact_for(ctx: Ctx, row: Any, exact: PermissionScope) -> bool:
    return (
        row.created_by == ctx.user.id
        or exact.is_global
        or row.region_id in exact.region_ids
        or (row.station_id is not None and row.station_id in exact.station_ids)
    )


def _out(row: Any, ctx: Ctx, exact: PermissionScope | None = None) -> RecordOut:
    precise = exact is None or _exact_for(ctx, row, exact)
    lat, lon = (row.lat, row.lon) if precise else (row.slat, row.slon)
    return RecordOut(
        id=row.id,
        kind=row.record_kind,
        title=row.title,
        occurred_at=row.occurred_at,
        region=row.region,
        station=row.station,
        beat=row.beat,
        locality=row.locality,
        location=PointIn(lat=lat, lon=lon) if lat is not None else None,
        precision=row.precision_level if precise else "GRID",
        confidence=float(row.confidence),
        reason=row.reason,
        evidence_reference=row.evidence_reference,
        visibility=row.visibility_classification,
        subject_incident_id=row.subject_incident_id,
        review_status=row.review_status,
        review_note=row.review_note,
        created_at=row.created_at,
        created_by_me=row.created_by == ctx.user.id,
    )


async def _flag(ctx: Ctx, key: str) -> bool:
    async with ctx.services.sessions() as session:
        enabled = (
            await session.execute(
                text("SELECT enabled FROM core.feature_flag WHERE key = :k"), {"k": key}
            )
        ).scalar_one_or_none()
    return bool(enabled)


@router.post("/operational-records", status_code=201, response_model=RecordOut)
async def create_record(ctx: CreateCtx, body: RecordIn) -> Any:
    if body.kind == "CIVIC_CONTEXT_REFERENCE" and not await _flag(ctx, "civic_context"):
        raise ProblemError(503, "feature-disabled", "Civic-context records are disabled")
    if body.kind == "ACTION_COMPLETION":
        raise ProblemError(
            422,
            "validation-error",
            "Record action completion on the approved action in the Review Queue",
        )
    async with ctx.uow() as uow:
        session = uow.session
        units = await load_region(session, body.region)
        if units is None:
            raise refuse(ctx, "opsrecord.create")
        station = units.stations.get(body.station) if body.station else None
        if body.station and station is None:
            raise refuse(ctx, "opsrecord.create")
        beat_id = None
        if body.location is not None:
            hit = (
                await session.execute(
                    text(
                        "SELECT b.org_unit_id, b.parent_id FROM core.current_boundary b "
                        "WHERE b.unit_type = 'BEAT' AND ST_Covers(b.geometry, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)) "
                        "AND b.parent_id IN (SELECT id FROM core.organizational_unit WHERE parent_id = :region) "
                        "ORDER BY b.code LIMIT 1"
                    ),
                    {"lon": body.location.lon, "lat": body.location.lat, "region": units.region.id},
                )
            ).one_or_none()
            if hit is None:
                raise ProblemError(
                    422, "validation-error", "The map point is outside the selected region"
                )
            if station is not None and hit.parent_id != station.id:
                raise ProblemError(
                    422, "validation-error", "The map point is outside the selected station"
                )
            beat_id = hit.org_unit_id
            station = next(s for s in units.stations.values() if s.id == hit.parent_id)
        target = Target(region_id=units.region.id, station_id=station.id if station else None)
        ctx.require("opsrecord.create", target=target)
        if body.subject_incident_id is not None:
            # Visible under RLS, in the caller's territory, sensitive only with that access, and in
            # the record's own region; anything else is the same audited 404 (no existence oracle).
            subject = (
                await session.execute(
                    text(
                        "SELECT i.region_id, i.station_id, coalesce(c.is_sensitive, false) AS sensitive "
                        "FROM core.incident_record i "
                        "LEFT JOIN core.analytical_category c ON c.id = i.analytical_category_id "
                        "WHERE i.id = :id"
                    ),
                    {"id": body.subject_incident_id},
                )
            ).one_or_none()
            subject_target = (
                Target(region_id=subject.region_id, station_id=subject.station_id)
                if subject
                else None
            )
            if (
                subject is None
                or subject.region_id != units.region.id
                or not authorize(
                    ctx.subject, "incident.summary.read", target=subject_target
                ).allowed
                or (
                    subject.sensitive
                    and not authorize(
                        ctx.subject, "incident.sensitive_category.read", target=subject_target
                    ).allowed
                )
            ):
                await session.rollback()
                raise await ctx.deny(
                    ctx.event(
                        "OPERATIONAL_RECORD_CREATE",
                        "DENY",
                        reason="SUBJECT_NOT_FOUND_IN_SCOPE",
                        object_type="incident",
                        object_id=str(body.subject_incident_id),
                    ),
                    404,
                    "not-found",
                    "Record not found",
                )
        review = "PENDING" if body.kind in NEEDS_REVIEW else "NOT_REQUIRED"
        record_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO core.operational_record (record_kind, title, occurred_at, geometry, locality,
                        precision_level, confidence, reason, evidence_reference, visibility_classification,
                        subject_incident_id, review_status, region_id, station_id, beat_id, created_by, is_demo)
                    VALUES (:kind, :title, :occurred_at,
                            CASE WHEN CAST(:lon AS float8) IS NULL THEN NULL
                                 ELSE ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) END,
                            :locality, :precision, :confidence, :reason, :evidence, :visibility,
                            :subject, :review, :region, :station, :beat, :user, :is_demo)
                    RETURNING id
                    """
                ),
                {
                    "kind": body.kind,
                    "title": body.title,
                    "occurred_at": body.occurred_at,
                    "lon": body.location.lon if body.location else None,
                    "lat": body.location.lat if body.location else None,
                    "locality": body.locality,
                    "precision": body.precision,
                    "confidence": body.confidence,
                    "reason": body.reason,
                    "evidence": body.evidence_reference,
                    "visibility": body.visibility,
                    "subject": body.subject_incident_id,
                    "review": review,
                    "region": units.region.id,
                    "station": station.id if station else None,
                    "beat": beat_id,
                    "user": ctx.user.id,
                    "is_demo": ctx.services.settings.environment != "PRODUCTION",
                },
            )
        ).scalar_one()
        uow.audit(
            ctx.event(
                "OPERATIONAL_RECORD_CREATE",
                object_type="operational_record",
                object_id=str(record_id),
                after_meta={
                    "kind": body.kind,
                    "review_status": review,
                    "region": body.region,
                    "station": station.code if station else None,
                    "has_point": body.location is not None,
                    "visibility": body.visibility,
                    "title_length": len(body.title),
                    "reason_length": len(body.reason),
                },
            )
        )
        row = (
            await session.execute(
                _SELECT,
                {
                    "id": record_id,
                    "status": None,
                    "kind": None,
                    "g": True,
                    "r": [],
                    "s": [],
                    "limit": 1,
                },
            )
        ).one()
        await uow.commit()
    return JSONResponse(status_code=201, content=_out(row, ctx).model_dump(mode="json"))


@router.get("/operational-records")
async def list_records(
    ctx: AuthenticatedCtx,
    review_status: Literal["NOT_REQUIRED", "PENDING", "APPROVED", "REJECTED"] | None = None,
    kind: Kind | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RecordList:
    """Pending records are listed for reviewers (``opsrecord.approve``); everything else for anyone
    who may create records, within the territory of that permission."""
    permission = "opsrecord.approve" if review_status == "PENDING" else "opsrecord.create"
    scope = permission_scope(ctx.subject, permission)
    if not scope.any:
        ctx.require(permission)
    if review_status == "PENDING" and all(
        g.role is Role.DATA_STEWARD
        for g in ctx.subject.grants
        if "opsrecord.approve" in ROLE_PERMISSIONS[g.role]
    ):
        # A steward's approve grant covers location corrections only: list only what they may decide.
        if kind not in (None, "LOCATION_CORRECTION_REQUEST"):
            return RecordList(data=[])
        kind = "LOCATION_CORRECTION_REQUEST"
    async with ctx.uow() as uow:
        rows = (
            await uow.session.execute(
                _SELECT,
                {
                    "id": None,
                    "status": review_status,
                    "kind": kind,
                    "g": scope.is_global,
                    "r": list(scope.region_ids),
                    "s": list(scope.station_ids),
                    "limit": limit,
                },
            )
        ).all()
        rows = [r for r in rows if _subject_visible(ctx, r)]
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="operational_record",
                filters={"review_status": review_status, "kind": kind},
                after_meta={"count": len(rows)},
            )
        )
        await uow.commit()
    exact = permission_scope(ctx.subject, "incident.location.exact", conditions=True)
    return RecordList(data=[_out(r, ctx, exact) for r in rows])


def _may_approve(ctx: Ctx, target: Target, kind: str) -> bool:
    """Approve grants from roles other than DATA_STEWARD cover every kind; a steward's covers
    location corrections only."""
    for g in ctx.subject.grants:
        if g.role is Role.DATA_STEWARD and kind != "LOCATION_CORRECTION_REQUEST":
            continue
        single = type(ctx.subject)(
            user_id=ctx.subject.user_id,
            grants=(g,),
            mfa=ctx.subject.mfa,
            auth_time=ctx.subject.auth_time,
        )
        if authorize(single, "opsrecord.approve", target=target).allowed:
            return True
    return False


@router.post("/operational-records/{record_id}/reviews")
async def review_record(ctx: ApproveCtx, record_id: UUID, body: ReviewIn) -> RecordOut:
    scope = permission_scope(ctx.subject, "opsrecord.approve")
    event_base: dict[str, Any] = {"object_type": "operational_record", "object_id": str(record_id)}
    async with ctx.uow() as uow:
        session = uow.session
        # Serialise concurrent reviews of one record: the second waits, then sees it decided (409).
        await session.execute(
            text("SELECT 1 FROM core.operational_record WHERE id = :id FOR UPDATE"),
            {"id": record_id},
        )
        row = (
            await session.execute(
                _SELECT,
                {
                    "id": record_id,
                    "status": None,
                    "kind": None,
                    "g": scope.is_global,
                    "r": list(scope.region_ids),
                    "s": list(scope.station_ids),
                    "limit": 1,
                },
            )
        ).one_or_none()
        if row is None or not _subject_visible(ctx, row):
            await session.rollback()
            raise await ctx.deny(
                ctx.event(
                    "OPERATIONAL_RECORD_REVIEW", "DENY", reason="NOT_FOUND_IN_SCOPE", **event_base
                ),
                404,
                "not-found",
                "Not found",
            )
        target = Target(region_id=row.region_id, station_id=row.station_id)
        if not _may_approve(ctx, target, row.record_kind):
            raise refuse(ctx, "opsrecord.approve")
        if row.created_by == ctx.user.id:
            await session.rollback()
            raise await ctx.deny(
                ctx.event(
                    "OPERATIONAL_RECORD_REVIEW", "DENY", reason="SEPARATION_OF_DUTIES", **event_base
                ),
                409,
                "separation-of-duties",
                "Records must be reviewed by someone other than their author",
            )
        if row.review_status != "PENDING":
            await session.rollback()
            raise await ctx.deny(
                ctx.event(
                    "OPERATIONAL_RECORD_REVIEW", "DENY", reason="ALREADY_DECIDED", **event_base
                ),
                409,
                "invalid-state-transition",
                "This record has already been reviewed",
            )
        decision = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
        await session.execute(
            text(
                "UPDATE core.operational_record SET review_status = :d, review_note = :n, reviewed_by = :u, "
                "reviewed_at = now(), updated_at = now() WHERE id = :id AND review_status = 'PENDING'"
            ),
            {"d": decision, "n": body.note, "u": ctx.user.id, "id": record_id},
        )
        uow.audit(
            ctx.event(
                "OPERATIONAL_RECORD_REVIEW",
                before_meta={"review_status": row.review_status},
                after_meta={
                    "review_status": decision,
                    "kind": row.record_kind,
                    "note_length": len(body.note),
                },
                **event_base,
            )
        )
        updated = (
            await session.execute(
                _SELECT,
                {
                    "id": record_id,
                    "status": None,
                    "kind": None,
                    "g": True,
                    "r": [],
                    "s": [],
                    "limit": 1,
                },
            )
        ).one()
        await uow.commit()
    exact = permission_scope(ctx.subject, "incident.location.exact", conditions=True)
    return _out(updated, ctx, exact)


@router.get("/map/operational-records")
async def map_records(
    ctx: PointsCtx,
    region: Annotated[str, Query(pattern=r"^[A-Z]{2,5}$")],
    period_from: date | None = None,
    period_to: date | None = None,
) -> dict[str, Any]:
    """Map layer: approved event markers and (unreviewed by design) observations with a point, in
    the caller's territory, optionally limited to the map's period (by occurrence, IST)."""
    scope = permission_scope(ctx.subject, "map.points.read")
    exact = permission_scope(ctx.subject, "incident.location.exact", conditions=True)
    if period_from and period_to and period_from > period_to:
        raise ProblemError(422, "validation-error", "period_from is after period_to")
    async with ctx.uow() as uow:
        units = await load_region(uow.session, region)
        if units is None or not region_allowed(scope, units):
            raise refuse(ctx, "map.points.read")
        rows = (
            await uow.session.execute(
                text(
                    """
                    SELECT o.id, o.record_kind, o.title, o.occurred_at, ST_X(o.geometry) AS lon,
                           ST_Y(o.geometry) AS lat, ST_X(sn.p) AS slon, ST_Y(sn.p) AS slat,
                           o.created_by, o.region_id, o.station_id
                    FROM core.operational_record o
                    JOIN core.organizational_unit r ON r.id = o.region_id
                    CROSS JOIN LATERAL (
                        SELECT ST_Transform(ST_SetSRID(ST_MakePoint(floor(ST_X(t.m) / 250) * 250 + 125,
                                                                    floor(ST_Y(t.m) / 250) * 250 + 125),
                                                       r.analysis_crs), 4326) AS p
                        FROM (SELECT ST_Transform(o.geometry, r.analysis_crs) AS m) t
                    ) sn
                    WHERE o.region_id = :region AND o.geometry IS NOT NULL
                      AND ((o.record_kind = 'APPROVED_EVENT_MARKER' AND o.review_status = 'APPROVED')
                           OR (o.record_kind = 'OPERATIONAL_OBSERVATION' AND o.review_status = 'NOT_REQUIRED'))
                      AND (:g OR o.region_id = ANY (CAST(:r AS uuid[])) OR o.station_id = ANY (CAST(:s AS uuid[])))
                      AND (CAST(:p_from AS timestamptz) IS NULL OR o.occurred_at >= :p_from)
                      AND (CAST(:p_to AS timestamptz) IS NULL OR o.occurred_at < :p_to)
                    ORDER BY o.occurred_at DESC LIMIT 500
                    """
                ),
                {
                    "region": units.region.id,
                    "g": scope.is_global,
                    "r": list(scope.region_ids),
                    "s": list(scope.station_ids),
                    "p_from": datetime.combine(period_from, time(), IST) if period_from else None,
                    "p_to": (
                        datetime.combine(period_to + timedelta(days=1), time(), IST)
                        if period_to
                        else None
                    ),
                },
            )
        ).all()
        uow.audit(
            ctx.event(
                "MAP_QUERY",
                object_type="operational_record_layer",
                filters={
                    "region": region,
                    "period_from": period_from.isoformat() if period_from else None,
                    "period_to": period_to.isoformat() if period_to else None,
                },
                after_meta={"count": len(rows)},
            )
        )
        await uow.commit()
    features = []
    for r in rows:
        lon, lat = (r.lon, r.lat) if _exact_for(ctx, r, exact) else (r.slon, r.slat)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "kind": r.record_kind,
                    "id": str(r.id),
                    "title": r.title,
                    "occurred_at": r.occurred_at.isoformat(),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


# --- analyst notes ---------------------------------------------------------------------------------


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_type: Literal["INCIDENT", "CELL", "EVIDENCE_SNAPSHOT", "AI_INSIGHT"]
    subject_id: UUID | None = None
    subject_key: str | None = Field(
        default=None, pattern=r"^[A-Z]{2,5}:\d{3,4}:-?\d{1,7}:-?\d{1,7}$"
    )
    note: str = Field(min_length=1, max_length=4000)
    visibility: Literal["INTERNAL", "RESTRICTED"]

    @model_validator(mode="after")
    def _subject(self) -> Self:
        if (self.subject_type in ("INCIDENT", "EVIDENCE_SNAPSHOT", "AI_INSIGHT")) != (
            self.subject_id is not None
        ):
            raise ValueError(
                "an INCIDENT, EVIDENCE_SNAPSHOT or AI_INSIGHT note needs subject_id; a CELL note needs subject_key"
            )
        if self.subject_type == "CELL" and self.subject_key is None:
            raise ValueError("a CELL note needs subject_key")
        return self


class NoteOut(BaseModel):
    id: UUID
    note: str
    visibility: str
    created_at: datetime
    created_by_me: bool


class NoteList(BaseModel):
    data: list[NoteOut]


async def _note_target(
    ctx: Ctx, subject_type: str, subject_id: UUID | None, key: str | None
) -> Target:
    async with ctx.uow() as uow:
        if subject_type == "EVIDENCE_SNAPSHOT":
            # An analysis note (Analytics Workbench): on a snapshot the caller may see.
            # Same rule as reading the snapshot itself (routes/analytics.may_read_snapshot).
            from crimemap_api.routes.analytics import may_read_snapshot

            row = (
                await uow.session.execute(
                    text(
                        "SELECT section, region_id, station_id, region_ids, station_ids, may_mask, created_by "
                        "FROM core.evidence_snapshot WHERE id = :id"
                    ),
                    {"id": subject_id},
                )
            ).one_or_none()
            target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
            ok = row is not None and may_read_snapshot(ctx, row)
        elif subject_type == "AI_INSIGHT":
            # "Add Note" on an insight card: on an insight the caller may see (routes/ai.may_see).
            from crimemap_api.routes.ai import may_see

            row = (
                await uow.session.execute(
                    text(
                        "SELECT insight_type, region_id, station_id FROM core.ai_insight WHERE id = :id"
                    ),
                    {"id": subject_id},
                )
            ).one_or_none()
            target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
            ok = row is not None and may_see(ctx, row)
        elif subject_type == "INCIDENT":
            row = (
                await uow.session.execute(
                    text(
                        "SELECT region_id, station_id, is_sensitive_category FROM core.incident_record WHERE id = :id"
                    ),
                    {"id": subject_id},
                )
            ).one_or_none()
            target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
            ok = (
                row is not None
                and authorize(ctx.subject, "incident.summary.read", target=target).allowed
                and (
                    not row.is_sensitive_category
                    or authorize(
                        ctx.subject, "incident.sensitive_category.read", target=target
                    ).allowed
                )
            )
        else:
            # A cell belongs to the station whose boundary holds its centre, so station-scoped users
            # can annotate (and see notes on) cells in their own station, and nobody outside can.
            region_code, size, q, r_ = (key or "::0:0").split(":")
            units = await load_region(uow.session, region_code)
            target = None
            if units is not None and size.isdigit() and int(size) in (250, 500, 1000, 2000):
                station_id = (
                    await uow.session.execute(
                        text(
                            "SELECT b.org_unit_id FROM core.current_boundary b WHERE b.unit_type = 'STATION' "
                            "AND b.parent_id = :region AND ST_Covers(b.geometry, "
                            "ST_Transform(ST_Centroid(core.hex_polygon(:q, :r, :size, :crs)), 4326)) "
                            "ORDER BY b.code LIMIT 1"
                        ),
                        {
                            "region": units.region.id,
                            "q": int(q),
                            "r": int(r_),
                            "size": float(size),
                            "crs": units.crs,
                        },
                    )
                ).scalar_one_or_none()
                target = Target(region_id=units.region.id, station_id=station_id)
            ok = (
                target is not None
                and authorize(ctx.subject, "opsrecord.create", target=target).allowed
            )
        await uow.commit()
    if not ok or target is None:
        raise await ctx.deny(
            ctx.event("NOTE_VIEW", "DENY", reason="NOT_FOUND_IN_SCOPE", object_type="analyst_note"),
            404,
            "not-found",
            "Not found",
        )
    return target


@router.post("/notes", status_code=201, response_model=NoteOut)
async def create_note(ctx: CreateCtx, body: NoteIn) -> Any:
    target = await _note_target(ctx, body.subject_type, body.subject_id, body.subject_key)
    ctx.require("opsrecord.create", target=target)
    async with ctx.uow() as uow:
        row = (
            await uow.session.execute(
                text(
                    "INSERT INTO core.analyst_note (subject_type, subject_id, subject_key, note, visibility_level, "
                    "region_id, station_id, created_by) VALUES (:t, :sid, :key, :note, :vis, :region, :station, :user) "
                    "RETURNING id, created_at"
                ),
                {
                    "t": body.subject_type,
                    "sid": body.subject_id,
                    "key": body.subject_key,
                    "note": body.note,
                    "vis": body.visibility,
                    "region": target.region_id,
                    "station": target.station_id,
                    "user": ctx.user.id,
                },
            )
        ).one()
        uow.audit(
            ctx.event(
                "NOTE_CREATE",
                object_type="analyst_note",
                object_id=str(row.id),
                after_meta={"subject_type": body.subject_type, "length": len(body.note)},
            )
        )
        await uow.commit()
    out = NoteOut(
        id=row.id,
        note=body.note,
        visibility=body.visibility,
        created_at=row.created_at,
        created_by_me=True,
    )
    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))


@router.get("/notes")
async def list_notes(
    ctx: CreateCtx,
    subject_type: Literal["INCIDENT", "CELL", "EVIDENCE_SNAPSHOT", "AI_INSIGHT"],
    subject_id: UUID | None = None,
    subject_key: Annotated[str | None, Query(max_length=100)] = None,
) -> NoteList:
    await _note_target(ctx, subject_type, subject_id, subject_key)
    async with ctx.uow() as uow:
        rows = (
            await uow.session.execute(
                text(
                    "SELECT id, note, visibility_level, created_at, created_by FROM core.analyst_note "
                    "WHERE subject_type = :t AND (subject_id = :sid OR subject_key = :key) "
                    "ORDER BY created_at DESC LIMIT 200"
                ),
                {"t": subject_type, "sid": subject_id, "key": subject_key},
            )
        ).all()
        uow.audit(
            ctx.event(
                "NOTE_VIEW",
                object_type="analyst_note",
                filters={"subject_type": subject_type},
                after_meta={"count": len(rows)},
            )
        )
        await uow.commit()
    return NoteList(
        data=[
            NoteOut(
                id=r.id,
                note=r.note,
                visibility=r.visibility_level,
                created_at=r.created_at,
                created_by_me=r.created_by == ctx.user.id,
            )
            for r in reversed(rows)
        ]
    )
