"""Home Map data (Phase 3): filter metadata and the role-scoped map query (ADR-019, ADR-020, ADR-032).

``GET /map/features`` answers one question for one region (ADR-007): which records match these
filters, and how may this caller see them? Every statement reads the same ``core.map_selection``
rows, so the status bar, legend and features always agree (HM-1).

Visibility rules, in order:
- Territory: every named region/station/beat must be inside the scope of the permission in use
  (``map.points.read`` for points, ``map.aggregate.read`` for hexagons/beats); otherwise 403,
  audited. Row-level security is the second filter (HIGHLY_RESTRICTED records are invisible).
- Points: never residential (they are aggregated) and never a sensitive category unless the caller
  holds ``incident.sensitive_category.read`` there. Exact coordinates only within the scope of
  ``incident.location.exact`` (with its purpose, e.g. DQ_REVIEW for stewards); otherwise the point
  is the centre of its 250 m cell in the region's metric CRS. Above ``MAX_POINTS`` the answer is
  aggregated to hexagons.
- Sensitive categories without sensitive-category access appear only as a combined "sensitive"
  count, shown per cell (hexagons, beats) or for the whole selection (points) when it is at least 3
  (ADR-012). Sensitive records not shown that way are counted nowhere (not in the selected,
  no-location or outside totals), and the response says the same thing whether that is 0 or 2, so
  narrowing filters cannot reveal them. Such callers cannot filter on a sensitive category (audited
  403): one category on its own is not "combined".
- A view box selects points by the coordinate the caller receives (the snapped one for non-exact
  callers), so narrowing the box cannot locate the exact point.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.deps import AUTHENTICATED, Ctx
from crimemap_api.http import ProblemError
from crimemap_api.policy.catalogue import Purpose
from crimemap_api.policy.engine import PermissionScope, permission_scope
from crimemap_api.territory import (
    RegionUnits,
    load_region,
    refuse,
    region_allowed,
    resolve,
    station_allowed,
)

router = APIRouter(prefix="/api/v1", tags=["map"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]

IST = ZoneInfo("Asia/Kolkata")
MAX_POINTS = 3000
MAX_PERIOD_DAYS = 731
SNAP_M = 250
SMALL_CELL = 3
HEX_SIZES = (250, 500, 1000, 2000)
DATA_LABEL = "DEMO / SYNTHETIC DATA"
SENSITIVE_BUCKET = "SENSITIVE"
SUPPRESSION_LABEL = f"sensitive records are counted only in groups of {SMALL_CELL} or more"
EARLIEST = date(2000, 1, 1)

Aggregation = Literal["POINTS", "HEX", "BEAT"]
TimeBasis = Literal["OCCURRENCE", "REPORTED"]
RecordType = Literal["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT"]
RegionCode = Annotated[str, Query(pattern=r"^[A-Z]{2,5}$")]
UnitCodes = Annotated[list[str], Query(max_length=50)]


# --- filter metadata ---------------------------------------------------------------------------


class FilterUnitOut(BaseModel):
    code: str
    name: str


class FilterBeatOut(FilterUnitOut):
    station_code: str


class CategoryOut(BaseModel):
    code: str
    label: str
    is_sensitive: bool
    points_allowed: bool


class StatusOut(BaseModel):
    record_type: str
    code: str
    label: str


class FilterMetadata(BaseModel):
    data_label: str
    region: str
    regions: list[FilterUnitOut]
    stations: list[FilterUnitOut]
    beats: list[FilterBeatOut]
    categories: list[CategoryOut]
    record_types: list[str]
    statuses: list[StatusOut]
    confidence_bands: list[float]
    aggregations: list[str]
    hex_sizes_m: list[int]
    exact_locations: bool
    default_period_days: int


async def _regions(session: AsyncSession) -> list[tuple[UUID, str, str]]:
    rows = await session.execute(
        text(
            "SELECT id, code, name FROM core.organizational_unit WHERE unit_type = 'REGION' ORDER BY code"
        )
    )
    return [(r.id, r.code, r.name) for r in rows]


def _scope_for_map(ctx: Ctx) -> tuple[PermissionScope, PermissionScope]:
    return (
        permission_scope(ctx.subject, "map.aggregate.read"),
        permission_scope(ctx.subject, "map.points.read"),
    )


@router.get("/filters/metadata")
async def filter_metadata(
    ctx: AuthenticatedCtx, region: RegionCode | None = None
) -> FilterMetadata:
    """Filter values inside the caller's map territory only."""
    aggregate, points = _scope_for_map(ctx)
    if not (aggregate.any or points.any):
        ctx.require("map.aggregate.read")  # raises: audited 403
    async with ctx.services.sessions() as session:
        allowed: list[tuple[str, str, RegionUnits]] = []
        for _id, code, name in await _regions(session):
            units = await load_region(session, code)
            if units and (region_allowed(aggregate, units) or region_allowed(points, units)):
                allowed.append((code, name, units))
        if not allowed:
            raise refuse(ctx, "map.aggregate.read")
        chosen = next((a for a in allowed if a[0] == region), None) if region else allowed[0]
        if chosen is None:
            raise refuse(ctx, "map.aggregate.read")
        units = chosen[2]
        scope = points if region_allowed(points, units) else aggregate
        stations = [s for s in units.stations.values() if station_allowed(scope, units, s)]
        station_ids = {s.id for s in stations}
        beats = [b for b in units.beats.values() if b.parent_id in station_ids]
        categories = (
            await session.execute(
                text("SELECT code, name, is_sensitive FROM core.analytical_category ORDER BY name")
            )
        ).all()
        statuses = (
            await session.execute(
                text(
                    "SELECT record_type, code, label FROM core.status_definition ORDER BY record_type, code"
                )
            )
        ).all()
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    exact = permission_scope(ctx.subject, "incident.location.exact", conditions=True)
    sensitive_everywhere = covers(sensitive, units, None)
    by_station = {s.id: s.code for s in stations}
    return FilterMetadata(
        data_label=DATA_LABEL,
        region=units.region.code,
        regions=[FilterUnitOut(code=c, name=n) for c, n, _ in allowed],
        stations=[FilterUnitOut(code=s.code, name=s.name) for s in stations],
        beats=[
            FilterBeatOut(code=b.code, name=b.name, station_code=by_station[b.parent_id])
            for b in beats
            if b.parent_id is not None
        ],
        # Without sensitive-category access those categories exist only as the combined bucket,
        # so they are not offered as filters (map/features refuses them).
        categories=[
            CategoryOut(
                code=c.code,
                label=c.name,
                is_sensitive=c.is_sensitive,
                points_allowed=not c.is_sensitive or sensitive_everywhere,
            )
            for c in categories
            if not c.is_sensitive or sensitive_everywhere
        ],
        record_types=["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT"],
        statuses=[
            StatusOut(record_type=s.record_type, code=s.code, label=s.label) for s in statuses
        ],
        confidence_bands=[0, 0.4, 0.8],
        aggregations=(["POINTS"] if region_allowed(points, units) else []) + ["HEX", "BEAT"],
        hex_sizes_m=list(HEX_SIZES),
        exact_locations=region_allowed(exact, units),
        default_period_days=90,
    )


# --- map features ------------------------------------------------------------------------------

# The selection call is spelled out in every statement (literal SQL, see ADR-027 note).
_GROUPS = text(
    """
SELECT (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked,
       s.geometry IS NULL AS no_location,
       s.geometry IS NOT NULL AND s.beat_id IS NULL AS outside,
       s.is_residential AS residential,
       s.quality <> 'OK' AS dq,
       count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
                        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
                        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        CAST(:min_conf AS numeric), CAST(:sg AS boolean), CAST(:sr AS uuid[]),
                        CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3, 4, 5
"""
)

_POINTS = text(
    """
SELECT s.id, c.code AS category_code, c.name AS category_label, s.record_type, s.quality,
       ex.exact,
       CASE WHEN ex.exact THEN ST_X(s.geometry) ELSE ST_X(sn.p) END AS lon,
       CASE WHEN ex.exact THEN ST_Y(s.geometry) ELSE ST_Y(sn.p) END AS lat,
       count(*) OVER () AS in_view
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
                        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
                        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        CAST(:min_conf AS numeric), CAST(:sg AS boolean), CAST(:sr AS uuid[]),
                        CAST(:ss AS uuid[])) s
LEFT JOIN core.analytical_category c ON c.id = s.analytical_category_id
-- exact: an unconditional exact-location grant, or a purpose-bound one (the steward's, under
-- DQ_REVIEW) for a record whose location is under data-quality review.
CROSS JOIN LATERAL (SELECT (CAST(:ex_g AS boolean) OR s.region_id = ANY (CAST(:ex_r AS uuid[]))
                            OR s.station_id = ANY (CAST(:ex_s AS uuid[])))
                           OR s.id = ANY (CAST(:under_review AS uuid[])) AS exact) ex
CROSS JOIN LATERAL (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(
        floor(s.x / 250) * 250 + 125, floor(s.y / 250) * 250 + 125), s.crs), 4326) AS p) sn
WHERE s.geometry IS NOT NULL AND s.beat_id IS NOT NULL AND NOT s.is_residential
  AND NOT (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                   OR s.station_id = ANY (CAST(:sens_s AS uuid[]))))
  AND (CAST(:bbox AS float8[]) IS NULL OR (
        -- index-friendly pre-filter, widened by more than half a 250 m cell ...
        s.geometry && ST_Expand(ST_MakeEnvelope(
            (CAST(:bbox AS float8[]))[1], (CAST(:bbox AS float8[]))[2],
            (CAST(:bbox AS float8[]))[3], (CAST(:bbox AS float8[]))[4], 4326), 0.005)
        -- ... then the coordinate the caller actually receives (never the exact one for snapped callers)
        AND (CASE WHEN ex.exact THEN s.geometry ELSE sn.p END) && ST_MakeEnvelope(
            (CAST(:bbox AS float8[]))[1], (CAST(:bbox AS float8[]))[2],
            (CAST(:bbox AS float8[]))[3], (CAST(:bbox AS float8[]))[4], 4326)))
ORDER BY s.id
LIMIT :limit
"""
)

# Hexagons and beats read the selection once: the same grouped rows give the status-bar
# breakdown (flags) and the per-cell counts (cell columns are NULL for unusable rows).
# Records whose location is under data-quality review, in the purpose-bound exact scope. Looked up
# once (only when a purpose is declared) rather than per point: per-row EXISTS hit the statement
# timeout on the 200k demo set (Phase 3 audit).
_UNDER_REVIEW = text(
    """
SELECT DISTINCT d.incident_id FROM core.data_quality_issue d
WHERE d.incident_id IS NOT NULL AND d.state IN ('OPEN', 'ASSIGNED')
  AND d.issue_type IN ('LOW_CONFIDENCE_LOCATION', 'MISSING_LOCATION', 'OUTSIDE_JURISDICTION')
  AND d.region_id = :region
  AND (:g OR d.region_id = ANY (CAST(:r AS uuid[])) OR d.station_id = ANY (CAST(:s AS uuid[])))
"""
)

_HEX = text(
    """
WITH s AS (
    SELECT CASE WHEN s.geometry IS NOT NULL AND s.beat_id IS NOT NULL THEN s.x END AS x,
           CASE WHEN s.geometry IS NOT NULL AND s.beat_id IS NOT NULL THEN s.y END AS y,
           s.analytical_category_id, s.quality <> 'OK' AS dq,
           s.geometry IS NULL AS no_location, s.geometry IS NOT NULL AND s.beat_id IS NULL AS outside,
           s.is_residential AS residential,
           (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                    OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked
    FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
                            CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
                            CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                            CAST(:min_conf AS numeric), CAST(:sg AS boolean), CAST(:sr AS uuid[]),
                            CAST(:ss AS uuid[])) s
),
f AS (SELECT (sqrt(3) / 3 * x - y / 3) / :size AS q, (2.0 / 3 * y) / :size AS r, * FROM s),
g AS (SELECT -q - r AS cy, round(q) AS rq, round(r) AS rr, round(-q - r) AS ry, * FROM f),
h AS (SELECT CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN -ry - rr ELSE rq END AS hq,
             CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN rr
                  WHEN abs(ry - cy) > abs(rr - r) THEN rr ELSE -rq - ry END AS hr, * FROM g)
SELECT h.hq::int AS cell_q, h.hr::int AS cell_r, c.code AS category_code, c.name AS category_label,
       h.masked, h.no_location, h.outside, h.residential, h.dq, count(*) AS n
FROM h LEFT JOIN core.analytical_category c ON c.id = h.analytical_category_id
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
"""
)

_HEX_POLYGONS = text(
    """
SELECT t.q, t.r, ST_AsGeoJSON(core.hex_polygon(t.q, t.r, :size, :crs), 6) AS geometry
FROM unnest(CAST(:qs AS int[]), CAST(:rs AS int[])) AS t(q, r)
"""
)

_BEAT = text(
    """
SELECT CASE WHEN s.geometry IS NOT NULL AND s.beat_id IS NOT NULL THEN s.beat_id END AS beat_id,
       c.code AS category_code, c.name AS category_label,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked,
       s.geometry IS NULL AS no_location, s.geometry IS NOT NULL AND s.beat_id IS NULL AS outside,
       s.is_residential AS residential, s.quality <> 'OK' AS dq, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
                        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
                        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        CAST(:min_conf AS numeric), CAST(:sg AS boolean), CAST(:sr AS uuid[]),
                        CAST(:ss AS uuid[])) s
LEFT JOIN core.analytical_category c ON c.id = s.analytical_category_id
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
"""
)

_BEAT_SHAPES = text(
    """
SELECT b.org_unit_id AS beat_id, b.code, b.name, ST_AsGeoJSON(b.geometry, 5) AS geometry
FROM core.current_boundary b WHERE b.org_unit_id = ANY (CAST(:ids AS uuid[]))
"""
)

_FRESHNESS = text(
    "SELECT max(last_batch_at) FROM core.source_system WHERE approved_status IN ('DEMO', 'APPROVED')"
)


def covers(scope: PermissionScope, units: RegionUnits, station_ids: list[UUID] | None) -> bool:
    """Does ``scope`` cover the whole selection (all of the region, or every selected station)?

    Decided from grants and the request only, never from the data, so the answer (and with it the
    shape of the response) cannot reveal whether sensitive records exist.
    """
    if scope.is_global or units.region.id in scope.region_ids:
        return True
    wanted = station_ids or [s.id for s in units.stations.values()]
    return bool(wanted) and all(sid in scope.station_ids for sid in wanted)


def _scope_args(prefix: str, scope: PermissionScope) -> dict[str, Any]:
    return {
        f"{prefix}g": scope.is_global,
        f"{prefix}r": list(scope.region_ids),
        f"{prefix}s": list(scope.station_ids),
    }


@dataclass
class Counts:
    selected: int = 0
    no_location: int = 0
    outside: int = 0
    usable: int = 0
    residential: int = 0


def _flag_groups(rows: Sequence[Any]) -> list[SimpleNamespace]:
    """Collapse combined (cell x category x flags) rows into the status-bar flag groups."""
    totals: dict[tuple[bool, bool, bool, bool, bool], int] = defaultdict(int)
    for r in rows:
        totals[(r.masked, r.no_location, r.outside, r.residential, r.dq)] += r.n
    return [
        SimpleNamespace(masked=m, no_location=nl, outside=o, residential=res, dq=dq, n=n)
        for (m, nl, o, res, dq), n in totals.items()
    ]


def _counts(groups: Sequence[Any]) -> Counts:
    """Status-bar counts over the records the caller may see individually (masked ones are left out)."""
    c = Counts()
    for g in groups:
        if g.masked:
            continue
        c.selected += g.n
        if g.no_location:
            c.no_location += g.n
        elif g.outside:
            c.outside += g.n
        else:
            c.usable += g.n
            if g.residential:
                c.residential += g.n
    return c


def _period(
    period_from: date | None, period_to: date | None
) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(IST).date()
    to = period_to or today
    start = period_from or to - timedelta(days=89)
    if start < EARLIEST or to > today + timedelta(days=366):
        raise ProblemError(
            422, "validation-error", "The period must lie between 2000 and next year"
        )
    if start > to:
        raise ProblemError(422, "validation-error", "period_from is after period_to")
    if (to - start).days + 1 > MAX_PERIOD_DAYS:
        raise ProblemError(422, "query-too-broad", "The period may be at most two years")
    return (
        start,
        to,
        datetime.combine(start, time(), IST),
        datetime.combine(to + timedelta(days=1), time(), IST),
    )


def _bbox(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parts = [float(p) for p in value.split(",")]
    except ValueError:
        raise ProblemError(
            422, "validation-error", "bbox must be minLon,minLat,maxLon,maxLat"
        ) from None
    if len(parts) != 4 or not (
        -180 <= parts[0] < parts[2] <= 180 and -90 <= parts[1] < parts[3] <= 90
    ):
        raise ProblemError(422, "validation-error", "bbox must be minLon,minLat,maxLon,maxLat")
    return parts


class SuppressedOut(BaseModel):
    count: int | None
    label: str


class ExcludedOut(BaseModel):
    no_location: int
    outside_jurisdiction: int
    aggregate_only_residential: int
    aggregate_only_sensitive: int
    outside_view: int
    suppressed_small_cells: SuppressedOut


class MapSummaryOut(BaseModel):
    selected: int
    usable_locations: int
    shown: int
    dq_warnings: int
    excluded: ExcludedOut


class LegendItem(BaseModel):
    code: str
    label: str
    count: int


class AggregationOut(BaseModel):
    requested: str
    applied: str
    hex_size_m: int | None
    reason: str | None


class PeriodOut(BaseModel):
    period_from: date
    period_to: date
    basis: str


class AboutOut(BaseModel):
    spatial_unit: str
    method: str
    crs: str
    limitations: list[str]


class MapOut(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    data_label: str
    region: str
    features: list[dict[str, Any]]
    summary: MapSummaryOut
    legend: list[LegendItem]
    aggregation: AggregationOut
    location_precision: str
    period: PeriodOut
    last_refreshed_at: datetime | None
    about: AboutOut


def _suppression(may_mask: bool) -> SuppressedOut:
    # The same answer whether 0, 1 or 2 sensitive records were left out (no count is ever given).
    return (
        SuppressedOut(count=None, label=SUPPRESSION_LABEL)
        if may_mask
        else SuppressedOut(count=0, label="none")
    )


def _cells(
    rows: Sequence[Any], key: Any
) -> tuple[dict[Any, dict[str, Any]], dict[tuple[str, str], int]]:
    """Fold (unit, category, masked) rows into cells with ADR-012 suppression."""
    cells: dict[Any, dict[str, Any]] = {}
    masked: dict[Any, int] = defaultdict(int)
    for r in rows:
        k = key(r)
        if k is None or (isinstance(k, tuple) and k[0] is None):
            continue  # no usable location: counted in the status bar only
        cell = cells.setdefault(k, {"categories": defaultdict(int), "labels": {}, "dq": 0})
        if r.masked:
            masked[k] += r.n
        else:
            code = r.category_code or "UNMAPPED"
            cell["categories"][code] += r.n
            cell["labels"][code] = r.category_label or "Unmapped offence code"
            cell["dq"] += r.n if r.dq else 0
    legend: dict[tuple[str, str], int] = defaultdict(int)
    for k, cell in cells.items():
        m = masked.get(k, 0)
        shown_sensitive = m if m >= SMALL_CELL else None
        cell["sensitive"] = shown_sensitive
        cell["count"] = sum(cell["categories"].values()) + (shown_sensitive or 0)
        for code, n in cell["categories"].items():
            legend[(code, cell["labels"][code])] += n
        if shown_sensitive:
            legend[(SENSITIVE_BUCKET, "Sensitive categories (combined)")] += shown_sensitive
    return {k: c for k, c in cells.items() if c["count"] > 0}, legend


def _properties(kind: str, cell: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        **extra,
        "count": cell["count"],
        "categories": sorted(
            (
                {"code": c, "label": cell["labels"][c], "count": n}
                for c, n in cell["categories"].items()
            ),
            key=lambda item: -item["count"],
        ),
        "sensitive": {
            "count": cell["sensitive"],
            "label": str(cell["sensitive"]) if cell["sensitive"] else "not shown",
        },
        "dq_warnings": cell["dq"],
    }


@router.get("/map/features")
async def map_features(
    ctx: AuthenticatedCtx,
    region: RegionCode,
    station: UnitCodes = [],  # noqa: B006 - FastAPI query list default
    beat: UnitCodes = [],  # noqa: B006
    category: UnitCodes = [],  # noqa: B006
    record_type: Annotated[list[RecordType], Query(max_length=4)] = [],  # noqa: B006
    status: UnitCodes = [],  # noqa: B006
    period_from: date | None = None,
    period_to: date | None = None,
    time_basis: TimeBasis = "OCCURRENCE",
    min_confidence: Literal["0", "0.4", "0.8"] = "0",
    aggregation: Aggregation = "HEX",
    hex_size_m: Literal["250", "500", "1000", "2000"] = "500",
    bbox: Annotated[str | None, Query(max_length=120)] = None,
    purpose: Purpose | None = None,
) -> MapOut:
    permission = "map.points.read" if aggregation == "POINTS" else "map.aggregate.read"
    scope = permission_scope(ctx.subject, permission)
    if not scope.any:
        ctx.require(permission)  # raises: audited 403
    start, end, p_from, p_to = _period(period_from, period_to)
    bounds = _bbox(bbox)
    size = int(hex_size_m)

    async with ctx.uow() as uow:
        session = uow.session
        units = await load_region(session, region)
        selection = resolve(ctx, permission, scope, units, station, beat)
        units = selection.units
        sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
        may_mask = not covers(sensitive, units, selection.station_ids)
        category_ids: list[UUID] = []
        if category:
            found = {
                r.code: r
                for r in await session.execute(
                    text(
                        "SELECT code, id, is_sensitive FROM core.analytical_category WHERE code = ANY(:c)"
                    ),
                    {"c": category},
                )
            }
            if set(category) - set(found):
                raise ProblemError(422, "validation-error", "Unknown category")
            if may_mask and any(found[c].is_sensitive for c in category):
                raise refuse(ctx, "incident.sensitive_category.read")
            category_ids = [found[c].id for c in category]
        if status:
            known = {
                r[0]
                for r in await session.execute(
                    text("SELECT DISTINCT code FROM core.status_definition WHERE code = ANY(:c)"),
                    {"c": status},
                )
            }
            if set(status) - known:
                raise ProblemError(422, "validation-error", "Unknown status")

        # Grants usable without a purpose give exact points everywhere in their scope; grants that
        # need the declared purpose (DQ_REVIEW) only for records under geocode review.
        exact = permission_scope(ctx.subject, "incident.location.exact", conditions=True)
        exact_for_review = (
            permission_scope(
                ctx.subject, "incident.location.exact", conditions=True, purpose=purpose
            )
            if purpose
            else PermissionScope(False, frozenset(), frozenset())
        )
        params: dict[str, Any] = {
            "region": units.region.id,
            "stations": selection.station_ids,
            "beats": selection.beat_ids,
            "categories": category_ids or None,
            "record_types": list(record_type) or None,
            "statuses": list(status) or None,
            "p_from": p_from,
            "p_to": p_to,
            "basis": time_basis,
            "min_conf": float(min_confidence),
            **_scope_args("s", scope),
            **_scope_args("sens_", sensitive),
        }
        combined: Sequence[Any] | None = None
        if aggregation == "HEX":
            combined = (await session.execute(_HEX, {**params, "size": size})).all()
        elif aggregation == "BEAT":
            combined = (await session.execute(_BEAT, params)).all()
        groups = (
            _flag_groups(combined)
            if combined is not None
            else (await session.execute(_GROUPS, params)).all()
        )
        # Sensitive records the caller may not see individually ("masked") are kept out of every
        # count below; they come back only as combined buckets of at least SMALL_CELL.
        masked_total = sum(g.n for g in groups if g.masked)
        counts = _counts(groups)
        dq_total = sum(g.n for g in groups if g.dq and not g.masked)
        masked_counted = 0

        features: list[dict[str, Any]] = []
        legend: dict[tuple[str, str], int] = defaultdict(int)
        applied: str = aggregation
        reason = None
        precision = "AGGREGATED"
        outside_view = 0
        shown = 0
        if aggregation == "POINTS":
            under_review: list[UUID] = (
                [
                    row[0]
                    for row in await session.execute(
                        _UNDER_REVIEW,
                        {"region": units.region.id, **_scope_args("", exact_for_review)},
                    )
                ]
                if exact_for_review.any
                else []
            )
            rows = (
                await session.execute(
                    _POINTS,
                    {
                        **params,
                        **_scope_args("ex_", exact),
                        "under_review": under_review,
                        "bbox": bounds,
                        "limit": MAX_POINTS + 1,
                    },
                )
            ).all()
            in_view = rows[0].in_view if rows else 0
            if in_view > MAX_POINTS:
                applied, reason = "HEX", f"more than {MAX_POINTS} points in view; aggregated"
            else:
                eligible = counts.usable - counts.residential
                outside_view = eligible - in_view
                masked_counted = masked_total if masked_total >= SMALL_CELL else 0
                exact_flags = {r.exact for r in rows}
                precision = (
                    "EXACT"
                    if exact_flags == {True}
                    else "SNAPPED_250M"
                    if exact_flags <= {False}
                    else "MIXED"
                )
                for r in rows:
                    code = r.category_code or "UNMAPPED"
                    label = r.category_label or "Unmapped offence code"
                    legend[(code, label)] += 1
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                            "properties": {
                                "kind": "incident",
                                "id": str(r.id),
                                "category_code": code,
                                "category_label": label,
                                "record_type": r.record_type,
                                "dq_warning": r.quality != "OK",
                                "precision": "EXACT" if r.exact else "SNAPPED_250M",
                            },
                        }
                    )
                shown = len(features)
        if applied == "HEX":
            rows = (
                combined
                if combined is not None
                else (await session.execute(_HEX, {**params, "size": size})).all()
            )
            cells, legend = _cells(rows, key=lambda r: (r.cell_q, r.cell_r))
            if cells:
                qs, rs = zip(*cells.keys(), strict=True)
                shapes = {
                    (p.q, p.r): p.geometry
                    for p in await session.execute(
                        _HEX_POLYGONS,
                        {"qs": list(qs), "rs": list(rs), "size": size, "crs": units.crs},
                    )
                }
                for (q, r), cell in sorted(cells.items()):
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": json.loads(shapes[(q, r)]),
                            "properties": _properties(
                                "cell", cell, key=f"{units.region.code}:{size}:{q}:{r}"
                            ),
                        }
                    )
            shown = sum(c["count"] for c in cells.values())
            masked_counted = sum(c["sensitive"] or 0 for c in cells.values())
        elif applied == "BEAT":
            rows = (
                combined if combined is not None else (await session.execute(_BEAT, params)).all()
            )
            cells, legend = _cells(rows, key=lambda r: r.beat_id)
            shapes_rows = (
                (await session.execute(_BEAT_SHAPES, {"ids": list(cells.keys())})).all()
                if cells
                else []
            )
            for b in sorted(shapes_rows, key=lambda b: b.code):
                features.append(
                    {
                        "type": "Feature",
                        "geometry": json.loads(b.geometry),
                        "properties": _properties(
                            "beat", cells[b.beat_id], code=b.code, name=b.name
                        ),
                    }
                )
            shown = sum(c["count"] for c in cells.values())
            masked_counted = sum(c["sensitive"] or 0 for c in cells.values())

        if applied == "POINTS" and masked_counted:
            legend[(SENSITIVE_BUCKET, "Sensitive categories (combined, not shown as points)")] += (
                masked_counted
            )
        freshness = (await session.execute(_FRESHNESS)).scalar_one_or_none()

        summary = MapSummaryOut(
            selected=counts.selected + masked_counted,
            # in hexagons and beats the combined buckets are drawn in cells, so they are located
            usable_locations=counts.usable + (masked_counted if applied != "POINTS" else 0),
            shown=shown,
            dq_warnings=dq_total,
            excluded=ExcludedOut(
                no_location=counts.no_location,
                outside_jurisdiction=counts.outside,
                aggregate_only_residential=counts.residential if applied == "POINTS" else 0,
                aggregate_only_sensitive=masked_counted if applied == "POINTS" else 0,
                outside_view=outside_view,
                suppressed_small_cells=_suppression(may_mask),
            ),
        )
        uow.audit(
            ctx.event(
                "MAP_QUERY",
                object_type="map",
                purpose=purpose.value if purpose else None,
                filters={
                    "region": region,
                    "station": station,
                    "beat": beat,
                    "category": category,
                    "record_type": list(record_type),
                    "status": status,
                    "period_from": start.isoformat(),
                    "period_to": end.isoformat(),
                    "time_basis": time_basis,
                    "min_confidence": min_confidence,
                    "aggregation": aggregation,
                    "hex_size_m": size if aggregation == "HEX" else None,
                    "bbox": bounds,
                },
                after_meta={
                    "selected": summary.selected,
                    "usable": summary.usable_locations,
                    "shown": shown,
                    "applied": applied,
                    "precision": precision,
                    "suppressed": summary.excluded.suppressed_small_cells.label,
                },
            )
        )
        await uow.commit()

    limitations = [
        "Synthetic demo data and synthetic boundaries; not an official jurisdiction map.",
        "Records the caller is not authorised to see (including HIGHLY_RESTRICTED) are not counted.",
        "Residential locations are generalised to about 250 m and never shown as points.",
    ]
    if may_mask:
        limitations.append(
            f"Sensitive categories are combined and counted only where at least {SMALL_CELL} records "
            "fall in a cell (or, for points, in the whole selection); smaller numbers are not counted anywhere."
        )
    if time_basis == "OCCURRENCE":
        limitations.append(
            "Records without an occurrence time are not included; choose 'reported' time to include them."
        )
    if min_confidence != "0":
        limitations.append(
            "With a minimum location confidence, records without a location are not included."
        )
    if precision in ("SNAPPED_250M", "MIXED"):
        limitations.append(
            "Point locations are the centre of their 250 m cell, not the exact place."
        )
    unit_label = {
        "POINTS": "individual records",
        "HEX": f"hexagons of {size} m (circumradius)",
        "BEAT": "synthetic beats",
    }[applied]
    return MapOut(
        data_label=DATA_LABEL,
        region=units.region.code,
        features=features,
        summary=summary,
        legend=[
            LegendItem(code=c, label=label, count=n)
            for (c, label), n in sorted(legend.items(), key=lambda item: -item[1])
        ],
        aggregation=AggregationOut(
            requested=aggregation,
            applied=applied,
            hex_size_m=size if applied == "HEX" else None,
            reason=reason,
        ),
        location_precision=precision,
        period=PeriodOut(period_from=start, period_to=end, basis=time_basis),
        last_refreshed_at=freshness.astimezone(UTC) if freshness else None,
        about=AboutOut(
            spatial_unit=unit_label,
            method="Counts of matching records; hexagons are computed in the region's UTM zone.",
            crs=f"EPSG:{units.crs} for aggregation; WGS 84 for display",
            limitations=limitations,
        ),
    )
