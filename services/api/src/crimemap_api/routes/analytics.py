"""Analytics Workbench API (Phase 5): sections, evidence snapshots, pattern reviews (ADR-035).

- ``GET /analytics/{section}``: Overview, Location Trends, Case Trends, Comparison, Data Quality,
  each returning the standard ``{data, meta}`` envelope (api.md §2). Civic Trends is refused while
  its feature flag is off; AI Analysis arrives in Phase 7 (no endpoint).
- ``POST /evidence-snapshots``: re-runs an analysis from its parameters and stores the result as
  immutable evidence (hash recorded). A pattern review, an export and an analysis note all cite one.
- ``POST /pattern-reviews``: a draft pattern review from a Location Trends snapshot, with every
  field the specification requires; it describes places and periods, never people.
Every view and write is audited; denials are audited by the shared handlers.
"""

import hashlib
import json
import re
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.analytics.query import AnalyticsQuery, Section, section_scope
from crimemap_api.analytics.sections import SECTIONS
from crimemap_api.deps import Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.engine import PermissionScope, Target, permission_scope
from crimemap_api.reports.render import tables
from crimemap_api.routes.search import looks_personal

router = APIRouter(prefix="/api/v1", tags=["analytics"])
ReadCtx = Annotated[Ctx, Depends(Requirement("analytics.read"))]
CompareCtx = Annotated[Ctx, Depends(Requirement("analytics.compare"))]
PatternCtx = Annotated[Ctx, Depends(Requirement("pattern_review.create"))]
Filters = Annotated[AnalyticsQuery, Query()]


class Envelope(BaseModel):
    data: dict[str, Any]
    meta: dict[str, Any]


async def run_section(
    ctx: Ctx, session: AsyncSession, section: Section, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    return await SECTIONS[section](ctx, session, q)


async def _view(ctx: Ctx, section: Section, q: AnalyticsQuery) -> Envelope:
    async with ctx.uow() as uow:
        data, meta = await run_section(ctx, uow.session, section, q)
        meta.pop("_scope", None)
        uow.audit(
            ctx.event(
                "ANALYTICS_QUERY",
                object_type="analytics",
                object_id=section,
                filters=q.as_params(),
                after_meta={"records": meta["record_count"], "excluded": meta["excluded_count"]},
            )
        )
        await uow.commit()
    # The Table view shows exactly the aggregated tables an export of this result contains.
    data = data | {
        "tables": [{"title": t, "headers": h, "rows": r} for t, h, r in tables(section, data)]
    }
    return Envelope(data=data, meta=meta)


@router.get("/analytics/overview")
async def analytics_overview(ctx: ReadCtx, q: Filters) -> Envelope:
    return await _view(ctx, "overview", q)


@router.get("/analytics/location")
async def analytics_location(ctx: ReadCtx, q: Filters) -> Envelope:
    return await _view(ctx, "location", q)


@router.get("/analytics/case-trends")
async def analytics_case_trends(ctx: ReadCtx, q: Filters) -> Envelope:
    return await _view(ctx, "case_trends", q)


@router.get("/analytics/comparison")
async def analytics_comparison(ctx: CompareCtx, q: Filters) -> Envelope:
    return await _view(ctx, "comparison", q)


@router.get("/analytics/data-quality")
async def analytics_data_quality(ctx: ReadCtx, q: Filters) -> Envelope:
    return await _view(ctx, "data_quality", q)


@router.get("/analytics/civic")
async def analytics_civic(ctx: ReadCtx) -> Envelope:
    async with ctx.services.sessions() as session:
        enabled = (
            await session.execute(
                text("SELECT enabled FROM core.feature_flag WHERE key = 'civic_context'")
            )
        ).scalar_one_or_none()
    if not enabled:
        raise ProblemError(
            503, "feature-disabled", "Civic Trends is disabled (feature flag civic_context is off)"
        )
    raise ProblemError(503, "feature-disabled", "No verified civic-context source is connected")


# --- evidence snapshots --------------------------------------------------------------------------


class SnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Section
    params: AnalyticsQuery


class SnapshotOut(BaseModel):
    id: UUID
    section: str
    params: dict[str, Any]
    data: dict[str, Any]
    meta: dict[str, Any]
    content_sha256: str
    created_at: Any
    created_by_me: bool


def digest(section: str, params: dict[str, Any], data: dict[str, Any], meta: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"section": section, "params": params, "data": data, "meta": meta},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


_INSERT_SNAPSHOT = text(
    """
INSERT INTO core.evidence_snapshot (section, params, data, meta, content_sha256, classification, region_id,
                                    station_id, region_ids, station_ids, may_mask, created_by)
VALUES (:section, CAST(:params AS jsonb), CAST(:data AS jsonb), CAST(:meta AS jsonb), :sha, 'RESTRICTED',
        (CAST(:regions AS uuid[]))[1], (CAST(:stations AS uuid[]))[1], CAST(:regions AS uuid[]),
        CAST(:stations AS uuid[]), :may_mask, :user)
RETURNING id, created_at
"""
)


async def store_snapshot(
    ctx: Ctx,
    session: AsyncSession,
    section: Section,
    q: AnalyticsQuery,
    data: dict[str, Any],
    meta: dict[str, Any],
) -> UUID:
    """Store a computed result with everything it covers; the caller audits and commits."""
    scope = meta.pop("_scope")
    params = q.as_params()
    sha = digest(section, params, data, meta)
    row = (
        await session.execute(
            _INSERT_SNAPSHOT,
            {
                "section": section,
                "params": json.dumps(params),
                "data": json.dumps(data, default=str),
                "meta": json.dumps(meta, default=str),
                "sha": sha,
                "regions": scope["region_ids"],
                "stations": scope["station_ids"],
                "may_mask": scope["may_mask"],
                "user": ctx.user.id,
            },
        )
    ).one()
    meta["evidence_snapshot_id"] = str(row.id)
    return row.id  # type: ignore[no-any-return]


async def create_snapshot(
    ctx: Ctx, session: AsyncSession, section: Section, q: AnalyticsQuery
) -> tuple[UUID, dict[str, Any], dict[str, Any]]:
    """Re-run the analysis from its parameters (with the caller's own access) and store it."""
    data, meta = await run_section(ctx, session, section, q)
    snapshot_id = await store_snapshot(ctx, session, section, q, data, meta)
    return snapshot_id, data, meta


@router.post("/evidence-snapshots", status_code=201)
async def post_snapshot(ctx: ReadCtx, body: SnapshotIn) -> JSONResponse:
    async with ctx.uow() as uow:
        snapshot_id, _data, meta = await create_snapshot(
            ctx, uow.session, body.section, body.params
        )
        uow.audit(
            ctx.event(
                "EVIDENCE_SNAPSHOT_CREATE",
                object_type="evidence_snapshot",
                object_id=str(snapshot_id),
                filters=body.params.as_params(),
                after_meta={"section": body.section, "records": meta["record_count"]},
            )
        )
        await uow.commit()
    return JSONResponse(status_code=201, content={"id": str(snapshot_id), "meta": meta})


_LOAD_SNAPSHOT = text(
    """
SELECT id, section, params, data, meta, content_sha256, created_at, created_by, region_id, station_id,
       region_ids, station_ids, may_mask
FROM core.evidence_snapshot WHERE id = :id
"""
)


def may_read_snapshot(ctx: Ctx, row: Any) -> bool:
    """The author, or a reader who could have produced the same result: the section's permission
    (``analytics.compare`` for comparisons; a steward only for Data Quality) over every unit the
    snapshot covers, and — if it was computed without combining sensitive categories — sensitive
    access over all of them too (Phase 5 audit: snapshots were readable on their first region alone)."""
    if row.created_by == ctx.user.id:
        return True
    permission = "analytics.compare" if row.section == "comparison" else "analytics.read"
    scope = section_scope(ctx, row.section, permission)
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")

    def covers(s: PermissionScope) -> bool:
        if s.is_global or all(r in s.region_ids for r in row.region_ids):
            return True
        return row.station_ids is not None and all(st in s.station_ids for st in row.station_ids)

    return covers(scope) and (row.may_mask or covers(sensitive))


async def load_snapshot(ctx: Ctx, session: AsyncSession, snapshot_id: UUID) -> Any:
    row = (await session.execute(_LOAD_SNAPSHOT, {"id": snapshot_id})).one_or_none()
    if row is None or not may_read_snapshot(ctx, row):
        raise await ctx.deny(
            ctx.event(
                "RECORD_VIEW",
                "DENY",
                reason="NOT_FOUND_IN_SCOPE",
                object_type="evidence_snapshot",
                object_id=str(snapshot_id),
            ),
            404,
            "not-found",
            "Not found",
        )
    return row


@router.get("/evidence-snapshots/{snapshot_id}")
async def get_snapshot(ctx: ReadCtx, snapshot_id: UUID) -> SnapshotOut:
    async with ctx.uow() as uow:
        row = await load_snapshot(ctx, uow.session, snapshot_id)
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="evidence_snapshot", object_id=str(snapshot_id))
        )
        await uow.commit()
    meta = dict(row.meta) | {"evidence_snapshot_id": str(row.id)}
    return SnapshotOut(
        id=row.id,
        section=row.section,
        params=row.params,
        data=row.data,
        meta=meta,
        content_sha256=row.content_sha256,
        created_at=row.created_at,
        created_by_me=row.created_by == ctx.user.id,
    )


# --- pattern reviews ----------------------------------------------------------------------------

PERSON_TERMS = re.compile(
    r"\b(suspects?|accused|offenders?|culprits?|perpetrators?|criminals?|gang members?|named (person|individual)|"
    r"his|her|he|she|mr|mrs|ms)\b",
    re.IGNORECASE,
)
CELL_KEY = r"^[A-Z]{2,5}:(250|500|1000|2000):-?\d{1,7}:-?\d{1,7}$"


class PatternReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    review_type: Literal["HOTSPOT", "EMERGING", "NEAR_REPEAT", "SERIES", "DATA_QUALITY"]
    evidence_snapshot_id: UUID
    cell_keys: list[Annotated[str, Field(pattern=CELL_KEY)]] = Field(min_length=1, max_length=200)
    narrative: str = Field(min_length=20, max_length=4000)
    recommended_next_step: str = Field(min_length=5, max_length=1000)

    @field_validator("cell_keys")
    @classmethod
    def _distinct(cls, keys: list[str]) -> list[str]:
        # A repeated cell would be counted twice in the review's record count (Phase 5 audit).
        if len(set(keys)) != len(keys):
            raise ValueError("each cell may be selected once")
        return keys


class PatternReviewOut(BaseModel):
    id: UUID
    review_type: str
    cell_keys: list[str]
    category: str
    period_start: Any
    period_end: Any
    baseline_start: Any
    baseline_end: Any
    record_count: int
    baseline_count: int | None
    quality_summary: dict[str, Any]
    evidence_snapshot_id: UUID
    narrative: str
    recommended_next_step: str
    analyst_status: str
    supervisor_status: str | None
    created_at: Any
    created_by_me: bool


class PatternReviewList(BaseModel):
    data: list[PatternReviewOut]


_INSERT_REVIEW = text(
    """
INSERT INTO core.pattern_review (review_type, grid_cell_ids, area_geometry, analytical_category_id, category_label,
    period_start, period_end, baseline_start, baseline_end, record_count, baseline_count, quality_summary,
    evidence_snapshot_id, analyst_narrative, recommended_next_step, region_id, station_id, created_by)
SELECT :type, CAST(:keys AS text[]),
       (SELECT ST_Multi(ST_Union(core.hex_polygon(t.q, t.r, :size, :crs)))
          FROM unnest(CAST(:qs AS int[]), CAST(:rs AS int[])) AS t(q, r)),
       :category, :category_label, :p_start, :p_end, :b_start, :b_end, :count, :baseline_count,
       CAST(:quality AS jsonb), :snapshot, :narrative, :next_step, :region, :station, :user
RETURNING id
"""
)

_LIST_REVIEWS = text(
    """
SELECT id, review_type, grid_cell_ids, category_label, period_start, period_end, baseline_start, baseline_end,
       record_count, baseline_count, quality_summary, evidence_snapshot_id, analyst_narrative,
       recommended_next_step, analyst_status, supervisor_status, created_at, created_by
FROM core.pattern_review
WHERE (CAST(:id AS uuid) IS NULL OR id = :id)
  AND (:g OR region_id = ANY (CAST(:r AS uuid[])) OR station_id = ANY (CAST(:s AS uuid[])))
ORDER BY created_at DESC LIMIT :limit
"""
)


def _review_out(row: Any, ctx: Ctx) -> PatternReviewOut:
    return PatternReviewOut(
        id=row.id,
        review_type=row.review_type,
        cell_keys=list(row.grid_cell_ids),
        category=row.category_label,
        period_start=row.period_start,
        period_end=row.period_end,
        baseline_start=row.baseline_start,
        baseline_end=row.baseline_end,
        record_count=row.record_count,
        baseline_count=row.baseline_count,
        quality_summary=row.quality_summary,
        evidence_snapshot_id=row.evidence_snapshot_id,
        narrative=row.analyst_narrative,
        recommended_next_step=row.recommended_next_step,
        analyst_status=row.analyst_status,
        supervisor_status=row.supervisor_status,
        created_at=row.created_at,
        created_by_me=row.created_by == ctx.user.id,
    )


@router.post("/pattern-reviews", status_code=201)
async def create_pattern_review(ctx: PatternCtx, body: PatternReviewIn) -> JSONResponse:
    for value in (body.narrative, body.recommended_next_step):
        if looks_personal(value) or PERSON_TERMS.search(value):
            raise ProblemError(
                422,
                "validation-error",
                "A pattern review describes places, times and records, not people: remove personal "
                "identifiers and references to individuals",
            )
    async with ctx.uow() as uow:
        session = uow.session
        snap = await load_snapshot(ctx, session, body.evidence_snapshot_id)
        params, data, meta = snap.params, snap.data, snap.meta
        if snap.section != "location" or data.get("unit") != "HEX":
            raise ProblemError(
                422, "validation-error", "Pattern reviews need a Location Trends hexagon snapshot"
            )
        comparison = meta["filters_applied"].get("comparison_period")
        if not comparison:
            raise ProblemError(
                422, "validation-error", "The snapshot needs a comparison (baseline) period"
            )
        cells = {f["properties"]["key"]: f["properties"] for f in data["map"]["features"]}
        missing = [k for k in body.cell_keys if k not in cells]
        if missing:
            raise ProblemError(
                422, "validation-error", "Every selected cell must be in the evidence snapshot"
            )
        from crimemap_api.territory import load_region

        units = await load_region(session, params["region"])
        if units is None:
            raise ProblemError(422, "validation-error", "Unknown region")
        station = (
            units.stations.get(params["station"][0])
            if len(params.get("station", [])) == 1
            else None
        )
        ctx.require(
            "pattern_review.create",
            target=Target(region_id=units.region.id, station_id=station.id if station else None),
        )
        size = int(body.cell_keys[0].split(":")[1])
        if any(int(k.split(":")[1]) != size for k in body.cell_keys):
            raise ProblemError(422, "validation-error", "Cells must share one grid size")
        category_id = None
        if len(params.get("category", [])) == 1:
            category_id = (
                await session.execute(
                    text("SELECT id FROM core.analytical_category WHERE code = :c"),
                    {"c": params["category"][0]},
                )
            ).scalar_one_or_none()
        p_start, p_end = (
            date.fromisoformat(d) for d in meta["filters_applied"]["period"].split("/")
        )
        b_start, b_end = (date.fromisoformat(d) for d in comparison.split("/"))
        count = sum(int(cells[k]["count"]) for k in body.cell_keys)
        baseline = sum(int(cells[k].get("comparison_count") or 0) for k in body.cell_keys)
        quality = {
            "data_quality_note": meta["data_quality_note"],
            "excluded_reasons": meta["excluded_reasons"],
            "suppressed": meta["suppressed"],
            "caveats": meta["caveats"],
        }
        review_id = (
            await session.execute(
                _INSERT_REVIEW,
                {
                    "type": body.review_type,
                    "keys": sorted(body.cell_keys),
                    "size": float(size),
                    "crs": units.crs,
                    "qs": [int(k.split(":")[2]) for k in body.cell_keys],
                    "rs": [int(k.split(":")[3]) for k in body.cell_keys],
                    "category": category_id,
                    "category_label": (
                        meta["filters_applied"].get("category")
                        and ", ".join(meta["filters_applied"]["category"])
                    )
                    or "All categories",
                    "p_start": p_start,
                    "p_end": p_end,
                    "b_start": b_start,
                    "b_end": b_end,
                    "count": count,
                    "baseline_count": baseline,
                    "quality": json.dumps(quality),
                    "snapshot": snap.id,
                    "narrative": body.narrative,
                    "next_step": body.recommended_next_step,
                    "region": units.region.id,
                    "station": station.id if station else None,
                    "user": ctx.user.id,
                },
            )
        ).scalar_one()
        uow.audit(
            ctx.event(
                "PATTERN_REVIEW_CREATE",
                object_type="pattern_review",
                object_id=str(review_id),
                after_meta={
                    "review_type": body.review_type,
                    "cells": len(body.cell_keys),
                    "record_count": count,
                    "evidence_snapshot_id": str(snap.id),
                    "narrative_length": len(body.narrative),
                },
            )
        )
        row = (
            await session.execute(
                _LIST_REVIEWS, {"id": review_id, "limit": 1, "g": True, "r": [], "s": []}
            )
        ).one()
        await uow.commit()
    return JSONResponse(status_code=201, content=_review_out(row, ctx).model_dump(mode="json"))


@router.get("/pattern-reviews")
async def list_pattern_reviews(
    ctx: PatternCtx, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> PatternReviewList:
    async with ctx.uow() as uow:
        # Only reviews inside the territory where the caller holds this permission (not the union
        # of all their grants, which is what row-level security sees).
        scope = permission_scope(ctx.subject, "pattern_review.create")
        rows = (
            await uow.session.execute(
                _LIST_REVIEWS,
                {
                    "id": None,
                    "limit": limit,
                    "g": scope.is_global,
                    "r": list(scope.region_ids),
                    "s": list(scope.station_ids),
                },
            )
        ).all()
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="pattern_review", after_meta={"count": len(rows)})
        )
        await uow.commit()
    return PatternReviewList(data=[_review_out(r, ctx) for r in rows])
