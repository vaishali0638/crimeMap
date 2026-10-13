"""PY Dashboard (Phase 4): station list, station details and the seven trend boards.

Everything here is an aggregate of the same ``core.map_selection`` rows the Home Map uses, under
``map.aggregate.read`` in its territory (the role matrix), with the same privacy rules:

- Territory: a named region or station outside the caller's scope is refused (audited 403), never
  silently narrowed. "All regions" means every region the caller may see.
- Sensitive categories without sensitive-category access covering the selection are combined and
  counted only in groups of at least 3 (per board row, per station); smaller groups are counted
  nowhere, and the response says the same thing whatever their size (ADR-012). Such callers cannot
  filter on one sensitive category.
- Rates need an approved denominator (ADR-014). The demo has none, so the station board ranks by
  count and says "Rate unavailable — no approved denominator"; nothing is invented.
- Wording is neutral: rankings are "higher recorded count in the selected period", never a judgement
  of an area, station or locality.

Every station-details and board view is audited (``ANALYTICS_QUERY``) with its filters and size.
Exports are not offered here: the boards say that viewing is audited and that export needs the
report permissions and re-authentication (Reports, Phase 5).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.deps import Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.engine import PermissionScope, Target, authorize, permission_scope
from crimemap_api.routes.map import (
    _FRESHNESS,
    DATA_LABEL,
    SMALL_CELL,
    SUPPRESSION_LABEL,
    TimeBasis,
    _period,
    _scope_args,
    covers,
)
from crimemap_api.territory import (
    RegionUnits,
    load_region,
    refuse,
    region_allowed,
    station_allowed,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])
PERMISSION = "map.aggregate.read"
DashboardCtx = Annotated[Ctx, Depends(Requirement(PERMISSION))]

Board = Literal["top-spot", "station-rate", "region", "location", "time", "month", "week"]
RegionFilter = Annotated[str, Query(pattern=r"^(ALL|[A-Z]{2,5})$")]
CategoryCodes = Annotated[list[str], Query(max_length=20)]

SPOT_SIZE_M = 500
TOP_ROWS = 20
RATE_UNAVAILABLE = "Rate unavailable — no approved denominator"
EXPORT_NOTICE = (
    "Viewing this board is recorded in the audit log. Export is not available here: exports need "
    "the report permissions and a fresh sign-in (Reports)."
)
BOARD_TITLES: dict[str, str] = {
    "top-spot": "Top Crime Spot",
    "station-rate": "Police Station Crime Rate",
    "region": "Region",
    "location": "Location",
    "time": "Time",
    "month": "Month",
    "week": "Week",
}


# --- SQL (literal statements; the selection call is spelled out in each, see map.py) ------------

_MASKED = """(s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                 OR s.station_id = ANY (CAST(:sens_s AS uuid[]))))"""

# Station x period x masked x location-usable x DQ: the scope totals, the quality caveat, and the
# station and region boards.
_STATIONS = text(
    """
SELECT s.station_id, s.event_at >= :cur_from AS current,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked,
       s.geometry IS NOT NULL AND s.beat_id IS NOT NULL AS usable, s.quality <> 'OK' AS dq, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3, 4, 5
"""
)

# 500 m hexagons (the same pointy-top axial arithmetic as map.py) with the most common locality
# among the records the caller may see individually, as a neutral place name.
_SPOTS = text(
    """
WITH s AS (
    SELECT s.x, s.y, s.event_at >= :cur_from AS current, s.locality,
           (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                    OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked
    FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                            NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                            0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
    WHERE s.geometry IS NOT NULL AND s.beat_id IS NOT NULL
),
f AS (SELECT (sqrt(3) / 3 * x - y / 3) / :size AS q, (2.0 / 3 * y) / :size AS r, * FROM s),
g AS (SELECT -q - r AS cy, round(q) AS rq, round(r) AS rr, round(-q - r) AS ry, * FROM f),
h AS (SELECT CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN -ry - rr ELSE rq END AS hq,
             CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN rr
                  WHEN abs(ry - cy) > abs(rr - r) THEN rr ELSE -rq - ry END AS hr, * FROM g)
SELECT h.hq::int AS cell_q, h.hr::int AS cell_r, h.current, h.masked, count(*) AS n,
       mode() WITHIN GROUP (ORDER BY h.locality) FILTER (WHERE NOT h.masked) AS locality
FROM h GROUP BY 1, 2, 3, 4
"""
)

_LOCATIONS = text(
    """
SELECT s.locality, s.station_id, s.event_at >= :cur_from AS current,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
WHERE s.locality IS NOT NULL
GROUP BY 1, 2, 3, 4
"""
)

# Time of day (IST hour), compared with the same hour in the previous period.
_HOURS = text(
    """
SELECT extract(hour FROM s.event_at AT TIME ZONE 'Asia/Kolkata')::int AS bucket, s.event_at >= :cur_from AS current,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3
"""
)

# Calendar months / ISO weeks (IST), each compared with the bucket before it.
_MONTHS = text(
    """
SELECT date_trunc('month', s.event_at AT TIME ZONE 'Asia/Kolkata')::date AS bucket, false AS current,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3
"""
)

_WEEKS = text(
    """
SELECT date_trunc('week', s.event_at AT TIME ZONE 'Asia/Kolkata')::date AS bucket, false AS current,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3
"""
)

_DETAILS = text(
    """
SELECT s.analytical_category_id AS category_id, s.record_type, s.status_code,
       (s.is_sensitive AND NOT (CAST(:sens_g AS boolean) OR s.region_id = ANY (CAST(:sens_r AS uuid[]))
                                OR s.station_id = ANY (CAST(:sens_s AS uuid[])))) AS masked, count(*) AS n
FROM core.map_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), NULL, CAST(:categories AS uuid[]),
                        NULL, NULL, CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text),
                        0, CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[])) s
GROUP BY 1, 2, 3, 4
"""
)

_JURISDICTION = text(
    """
SELECT b.version, b.approval_status, b.source, b.is_demo,
       round((ST_Area(b.geometry::geography) / 1e6)::numeric, 1) AS area_km2,
       (SELECT count(*) FROM core.organizational_unit o WHERE o.parent_id = b.org_unit_id
          AND o.unit_type = 'BEAT') AS beats
FROM core.current_boundary b WHERE b.org_unit_id = :station
"""
)

_DENOMINATORS = text(
    """
SELECT org_unit_id, sum(value) AS value FROM core.denominator
WHERE approval_status = 'APPROVED' AND measure = 'POPULATION'
  AND org_unit_id = ANY (CAST(:units AS uuid[])) AND period_start <= :to AND period_end >= :from
GROUP BY 1
"""
)

_CATEGORIES = text(
    "SELECT id, code, name, is_sensitive FROM core.analytical_category ORDER BY name"
)

_REGIONS = text(
    "SELECT code FROM core.organizational_unit WHERE unit_type = 'REGION' ORDER BY code"
)


# --- response models -----------------------------------------------------------------------------


class DashboardUnitOut(BaseModel):
    code: str
    name: str


class DashboardStationOut(DashboardUnitOut):
    region_code: str


class StationList(BaseModel):
    data_label: str
    regions: list[DashboardUnitOut]
    stations: list[DashboardStationOut]
    categories: list[DashboardUnitOut]


class DashboardPeriodOut(BaseModel):
    period_from: date
    period_to: date
    basis: str


class CountOut(BaseModel):
    code: str
    label: str
    count: int


class CombinedOut(BaseModel):
    count: int | None
    label: str


class CaseStatusOut(BaseModel):
    active: int
    detected: int
    closed: int


class JurisdictionOut(BaseModel):
    label: str
    beats: int
    area_km2: float | None
    boundary_version: int | None
    approval_status: str | None
    source: str | None


class StationDetailsOut(BaseModel):
    data_label: str
    station: DashboardUnitOut
    region: DashboardUnitOut
    jurisdiction: JurisdictionOut
    period: DashboardPeriodOut
    category: str
    selected: int
    cases: CaseStatusOut | None
    cases_note: str
    categories: list[CountOut]
    sensitive: CombinedOut | None
    last_refreshed_at: datetime | None


class MapTargetOut(BaseModel):
    region: str
    kind: Literal["region", "station", "cell"]
    code: str


class TrendRowOut(BaseModel):
    rank: int
    key: str
    label: str
    count: int
    previous: int | None
    change_pct: float | None
    movement: Literal["UP", "DOWN", "SAME", "NEW", "NONE"]
    rate: float | None
    rate_label: str | None
    partial: bool
    view_on_map: MapTargetOut | None


class QualityOut(BaseModel):
    selected: int
    usable_location_pct: float | None
    dq_warning_pct: float | None
    caveat: str


class RateOut(BaseModel):
    available: bool
    label: str


class ScopeOut(BaseModel):
    region: str
    label: str


class TrendBoardOut(BaseModel):
    data_label: str
    board: str
    title: str
    period: DashboardPeriodOut
    category: str
    scope: ScopeOut
    measure: str
    comparison: str
    rows: list[TrendRowOut]
    total_rows: int
    quality: QualityOut
    rate: RateOut | None
    notice: str
    limitations: list[str]
    last_refreshed_at: datetime | None


# --- shared --------------------------------------------------------------------------------------


@dataclass
class Selection:
    """The regions (and stations) a request covers, all inside the caller's scope."""

    regions: list[RegionUnits]
    station_ids: list[UUID] | None
    category_ids: list[UUID] | None
    category_label: str
    may_mask: bool
    names: dict[UUID, tuple[str, str, str]] = field(
        default_factory=dict
    )  # id -> code, name, region


async def _categories(session: AsyncSession) -> list[Any]:
    return list((await session.execute(_CATEGORIES)).all())


async def _selection(
    ctx: Ctx,
    session: AsyncSession,
    scope: PermissionScope,
    region: str,
    station: str | None,
    category: list[str],
) -> Selection:
    if region == "ALL":
        codes = [r.code for r in await session.execute(_REGIONS)]
        loaded = [u for c in codes if (u := await load_region(session, c)) is not None]
        regions = [u for u in loaded if region_allowed(scope, u)]
        if not regions:
            raise refuse(ctx, PERMISSION)
    else:
        units = await load_region(session, region)
        if units is None or not region_allowed(scope, units):
            raise refuse(ctx, PERMISSION)
        regions = [units]
    station_ids: list[UUID] | None = None
    if station:
        hit = next(
            (
                u
                for u in regions
                if station in u.stations and station_allowed(scope, u, u.stations[station])
            ),
            None,
        )
        if hit is None:
            raise refuse(ctx, PERMISSION)
        regions, station_ids = [hit], [hit.stations[station].id]
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    may_mask = not all(covers(sensitive, u, station_ids) for u in regions)
    found = {c.code: c for c in await _categories(session)}
    if set(category) - set(found):
        raise ProblemError(422, "validation-error", "Unknown category")
    if may_mask and any(found[c].is_sensitive for c in category):
        raise refuse(ctx, "incident.sensitive_category.read")
    names: dict[UUID, tuple[str, str, str]] = {}
    for u in regions:
        names[u.region.id] = (u.region.code, u.region.name, u.region.code)
        for s in u.stations.values():
            if station_allowed(scope, u, s):
                names[s.id] = (s.code, s.name, u.region.code)
    return Selection(
        regions=regions,
        station_ids=station_ids,
        category_ids=[found[c].id for c in category] or None,
        category_label=", ".join(found[c].name for c in category) or "All categories",
        may_mask=may_mask,
        names=names,
    )


def _params(
    ctx: Ctx,
    scope: PermissionScope,
    sel: Selection,
    units: RegionUnits,
    window: tuple[datetime, datetime, datetime],
    basis: str,
) -> dict[str, Any]:
    p_from, cur_from, p_to = window
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    return {
        "region": units.region.id,
        "stations": sel.station_ids,
        "categories": sel.category_ids,
        "p_from": p_from,
        "cur_from": cur_from,
        "p_to": p_to,
        "basis": basis,
        **_scope_args("s", scope),
        **_scope_args("sens_", sensitive),
    }


def counted(unmasked: int, masked: int, may_mask: bool) -> int:
    """ADR-012 per row: masked (combined sensitive) records count only as a group of 3 or more."""
    # Applied whenever masked rows exist, whatever may_mask says (station-based sensitive access can
    # leave records without a station masked); for callers who see everything masked is always 0.
    del may_mask
    return unmasked + (masked if masked >= SMALL_CELL else 0)


def _movement(
    count: int, previous: int | None
) -> tuple[float | None, Literal["UP", "DOWN", "SAME", "NEW", "NONE"]]:
    if previous is None:
        return None, "NONE"
    if previous == 0:
        return None, "NEW" if count > 0 else "SAME"
    change = round((count - previous) / previous * 100, 1)
    return change, "UP" if count > previous else "DOWN" if count < previous else "SAME"


def _period_label(start: date, end: date) -> str:
    return f"{start.isoformat()} to {end.isoformat()}"


async def _freshness(session: AsyncSession) -> datetime | None:
    value = (await session.execute(_FRESHNESS)).scalar_one_or_none()
    return value.astimezone(UTC) if value else None


# --- station list --------------------------------------------------------------------------------


@router.get("/stations")
async def dashboard_stations(ctx: DashboardCtx) -> StationList:
    """Regions and stations in the caller's aggregate territory (reference data: not audited)."""
    scope = permission_scope(ctx.subject, PERMISSION)
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    async with ctx.services.sessions() as session:
        regions: list[DashboardUnitOut] = []
        stations: list[DashboardStationOut] = []
        everywhere = True
        for code in [r.code for r in await session.execute(_REGIONS)]:
            units = await load_region(session, code)
            if units is None or not region_allowed(scope, units):
                continue
            everywhere = everywhere and covers(sensitive, units, None)
            regions.append(DashboardUnitOut(code=units.region.code, name=units.region.name))
            stations += [
                DashboardStationOut(code=s.code, name=s.name, region_code=units.region.code)
                for s in sorted(units.stations.values(), key=lambda s: s.code)
                if station_allowed(scope, units, s)
            ]
        categories = [
            DashboardUnitOut(code=c.code, name=c.name)
            for c in await _categories(session)
            if not c.is_sensitive or (everywhere and regions)
        ]
    if not regions:
        raise refuse(ctx, PERMISSION)
    return StationList(
        data_label=DATA_LABEL, regions=regions, stations=stations, categories=categories
    )


# --- station details -----------------------------------------------------------------------------

ACTIVE = {"REGISTERED", "UNDER_INVESTIGATION"}


@router.get("/stations/{station_code}")
async def station_details(
    ctx: DashboardCtx,
    station_code: Annotated[str, Path(pattern=r"^[A-Z]{2,5}-S\d{2}$")],
    category: CategoryCodes = [],  # noqa: B006 - FastAPI query list default
    period_from: date | None = None,
    period_to: date | None = None,
    time_basis: TimeBasis = "OCCURRENCE",
) -> StationDetailsOut:
    scope = permission_scope(ctx.subject, PERMISSION)
    start, end, p_from, p_to = _period(period_from, period_to)
    async with ctx.uow() as uow:
        session = uow.session
        sel = await _selection(ctx, session, scope, "ALL", station_code, category)
        units = sel.regions[0]
        station = units.stations[station_code]
        rows = (
            await session.execute(
                _DETAILS, _params(ctx, scope, sel, units, (p_from, p_from, p_to), time_basis)
            )
        ).all()
        jurisdiction = (await session.execute(_JURISDICTION, {"station": station.id})).one_or_none()
        cats = {c.id: c for c in await _categories(session)}
        unmasked: dict[UUID | None, int] = defaultdict(int)
        masked_total = 0
        cases = {"active": 0, "detected": 0, "closed": 0}
        for r in rows:
            if r.masked:
                masked_total += r.n
                continue
            unmasked[r.category_id] += r.n
            if r.record_type == "CASE":
                group = (
                    "active"
                    if r.status_code in ACTIVE
                    else r.status_code.lower()
                    if r.status_code in ("DETECTED", "CLOSED")
                    else None
                )
                if group:
                    cases[group] += r.n
        combined = masked_total if masked_total >= SMALL_CELL else None
        selected = sum(unmasked.values()) + (combined or 0)
        may_see_cases = authorize(
            ctx.subject,
            "incident.summary.read",
            target=Target(region_id=units.region.id, station_id=station.id),
        ).allowed
        uow.audit(
            ctx.event(
                "ANALYTICS_QUERY",
                object_type="station_details",
                object_id=station_code,
                filters={
                    "station": station_code,
                    "category": category,
                    "period_from": start.isoformat(),
                    "period_to": end.isoformat(),
                    "time_basis": time_basis,
                },
                after_meta={"selected": selected, "cases_shown": may_see_cases},
            )
        )
        freshness = await _freshness(session)
        await uow.commit()
    categories = sorted(
        (
            CountOut(
                code=cats[cid].code if cid in cats else "UNMAPPED",
                label=cats[cid].name if cid in cats else "Unmapped offence code",
                count=n,
            )
            for cid, n in unmasked.items()
        ),
        key=lambda c: (-c.count, c.label),
    )
    return StationDetailsOut(
        data_label=DATA_LABEL,
        station=DashboardUnitOut(code=station.code, name=station.name),
        region=DashboardUnitOut(code=units.region.code, name=units.region.name),
        jurisdiction=JurisdictionOut(
            label="Synthetic boundary — not an official jurisdiction"
            if not jurisdiction or jurisdiction.is_demo
            else "Approved boundary",
            beats=int(jurisdiction.beats) if jurisdiction else 0,
            area_km2=float(jurisdiction.area_km2) if jurisdiction else None,
            boundary_version=jurisdiction.version if jurisdiction else None,
            approval_status=jurisdiction.approval_status if jurisdiction else None,
            source=jurisdiction.source if jurisdiction else None,
        ),
        period=DashboardPeriodOut(period_from=start, period_to=end, basis=time_basis),
        category=sel.category_label,
        selected=selected,
        cases=CaseStatusOut(**cases) if may_see_cases else None,
        cases_note=(
            "Case records only: active = registered or under investigation."
            + (" Sensitive-category cases are not included." if sel.may_mask else "")
            if may_see_cases
            else "Case status counts need record-summary access for this station."
        ),
        categories=categories,
        sensitive=(
            CombinedOut(count=combined, label=str(combined) if combined else SUPPRESSION_LABEL)
            if sel.may_mask
            else None
        ),
        last_refreshed_at=freshness,
    )


# --- trend boards --------------------------------------------------------------------------------


@dataclass
class Bucket:
    key: str
    label: str
    current_unmasked: int = 0
    current_masked: int = 0
    previous_unmasked: int = 0
    previous_masked: int = 0
    target: MapTargetOut | None = None
    partial: bool = False


def _fold(buckets: dict[str, Bucket], key: str, row: Any, make: Any) -> None:
    b = buckets.get(key)
    if b is None:
        b = buckets[key] = make()
    if row.current:
        if row.masked:
            b.current_masked += row.n
        else:
            b.current_unmasked += row.n
    elif row.masked:
        b.previous_masked += row.n
    else:
        b.previous_unmasked += row.n


def _rank(rows: Iterable[TrendRowOut], by_rate: bool) -> list[TrendRowOut]:
    ordered = sorted(rows, key=lambda r: (-(r.rate or 0) if by_rate else -r.count, r.label))
    for i, r in enumerate(ordered, start=1):
        r.rank = i
    return ordered


def _row(b: Bucket, may_mask: bool, previous: int | None) -> TrendRowOut:
    count = counted(b.current_unmasked, b.current_masked, may_mask)
    change, movement = _movement(count, previous)
    return TrendRowOut(
        rank=0,
        key=b.key,
        label=b.label,
        count=count,
        previous=previous,
        change_pct=change,
        movement=movement,
        rate=None,
        rate_label=None,
        partial=b.partial,
        view_on_map=b.target,
    )


def _calendar_rows(
    buckets: dict[date, list[int]],
    start: date,
    end: date,
    may_mask: bool,
    kind: Literal["month", "week"],
) -> list[TrendRowOut]:
    """Months/weeks inside the period, chronological, each compared with the bucket before it."""

    def step_back(d: date) -> date:
        if kind == "week":
            return d - timedelta(days=7)
        return (d.replace(day=1) - timedelta(days=1)).replace(day=1)

    def bucket_end(d: date) -> date:
        if kind == "week":
            return d + timedelta(days=6)
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)

    first = start - timedelta(days=start.weekday()) if kind == "week" else start.replace(day=1)
    rows: list[TrendRowOut] = []
    d = first
    while d <= end:
        um, m = buckets.get(d, [0, 0])
        pum, pm = buckets.get(step_back(d), [0, 0])
        partial = d < start or bucket_end(d) > end
        label = d.strftime("%b %Y") if kind == "month" else f"Week of {d.isoformat()}"
        count = counted(um, m, may_mask)
        previous = counted(pum, pm, may_mask)
        change, movement = _movement(count, previous)
        rows.append(
            TrendRowOut(
                rank=0,
                key=d.isoformat(),
                label=label + (" (partial)" if partial else ""),
                count=count,
                previous=previous,
                change_pct=change,
                movement=movement,
                rate=None,
                rate_label=None,
                partial=partial,
                view_on_map=None,
            )
        )
        d = bucket_end(d) + timedelta(days=1)
    return rows


MEASURES: dict[str, str] = {
    "top-spot": f"Records in each {SPOT_SIZE_M} m hexagon cell in the selected period (records with a usable location). A higher recorded count in the selected period is not a judgement of the place.",
    "station-rate": "Records per station in the selected period. A rate per 100,000 residents is shown only where an approved population denominator exists for the station and period.",
    "region": "Records per region in the selected period.",
    "location": "Records per locality (as recorded at source) in the selected period.",
    "time": "Records by hour of day (IST) on the selected time basis.",
    "month": "Records per calendar month (IST) inside the selected period, in date order.",
    "week": "Records per week (Monday start, IST) inside the selected period, in date order.",
}
COMPARISONS: dict[str, str] = {
    "month": "Change against the month before.",
    "week": "Change against the week before.",
}
DEFAULT_COMPARISON = "Change against the same-length period immediately before the selected one."


@router.get("/trends/{board}")
async def trend_board(
    ctx: DashboardCtx,
    board: Board,
    region: RegionFilter = "ALL",
    station: Annotated[str | None, Query(pattern=r"^[A-Z]{2,5}-S\d{2}$")] = None,
    category: CategoryCodes = [],  # noqa: B006
    period_from: date | None = None,
    period_to: date | None = None,
    time_basis: TimeBasis = "OCCURRENCE",
) -> TrendBoardOut:
    scope = permission_scope(ctx.subject, PERMISSION)
    start, end, cur_from, p_to = _period(period_from, period_to)
    length = (end - start).days + 1
    if board in ("month", "week"):
        # one bucket before the first (possibly partial) bucket, for its comparison
        p_from = cur_from - timedelta(days=62 if board == "month" else 14)
    else:
        p_from = cur_from - timedelta(days=length)
    window = (p_from, cur_from, p_to)
    async with ctx.uow() as uow:
        session = uow.session
        sel = await _selection(ctx, session, scope, region, station, category)
        totals = {"selected": 0, "usable": 0, "dq": 0}
        station_buckets: dict[str, Bucket] = {}
        region_buckets: dict[str, Bucket] = {}
        unassigned = 0  # records with no station (no usable location): in region totals only
        other: dict[str, Bucket] = {}
        calendar: dict[date, list[int]] = defaultdict(lambda: [0, 0])
        # Every station in the selection has a row, with 0 when nothing was recorded.
        for units in sel.regions:
            for s_ in units.stations.values():
                if s_.id in sel.names and (sel.station_ids is None or s_.id in sel.station_ids):
                    station_buckets[s_.code] = Bucket(
                        key=s_.code,
                        label=s_.name,
                        target=MapTargetOut(region=units.region.code, kind="station", code=s_.code),
                    )
        for units in sel.regions:
            params = _params(ctx, scope, sel, units, window, time_basis)
            for r in (await session.execute(_STATIONS, params)).all():
                if r.current and not r.masked:
                    totals["selected"] += r.n
                    totals["usable"] += r.n if r.usable else 0
                    totals["dq"] += r.n if r.dq else 0
                _fold(
                    region_buckets,
                    units.region.code,
                    r,
                    lambda u=units: Bucket(
                        key=u.region.code,
                        label=u.region.name,
                        target=MapTargetOut(
                            region=u.region.code, kind="region", code=u.region.code
                        ),
                    ),
                )
                if r.station_id is None:
                    unassigned += r.n if r.current and not r.masked else 0
                    continue
                if r.station_id not in sel.names:
                    continue
                code, name, region_code = sel.names[r.station_id]
                _fold(
                    station_buckets,
                    code,
                    r,
                    lambda code=code, name=name, region_code=region_code: Bucket(
                        key=code,
                        label=name,
                        target=MapTargetOut(region=region_code, kind="station", code=code),
                    ),
                )
            if board == "top-spot":
                for r in (await session.execute(_SPOTS, {**params, "size": SPOT_SIZE_M})).all():
                    key = f"{units.region.code}:{SPOT_SIZE_M}:{r.cell_q}:{r.cell_r}"
                    _fold(
                        other,
                        key,
                        r,
                        lambda key=key, code=units.region.code: Bucket(
                            key=key,
                            label="",
                            target=MapTargetOut(region=code, kind="cell", code=key),
                        ),
                    )
                    if r.locality and not other[key].label:
                        other[key].label = f"Cell near {r.locality}"
            elif board == "location":
                for r in (await session.execute(_LOCATIONS, params)).all():
                    if r.station_id not in sel.names:
                        continue
                    code, _name, region_code = sel.names[r.station_id]
                    key = f"{code}:{r.locality}"
                    _fold(
                        other,
                        key,
                        r,
                        lambda key=key, loc=r.locality, code=code, region_code=region_code: Bucket(
                            key=key,
                            label=f"{loc} ({code})",
                            target=MapTargetOut(region=region_code, kind="station", code=code),
                        ),
                    )
            elif board == "time":
                for r in (await session.execute(_HOURS, params)).all():
                    key = f"{int(r.bucket):02d}"
                    _fold(
                        other,
                        key,
                        r,
                        lambda key=key: Bucket(key=key, label=f"{key}:00 to {key}:59 IST"),
                    )
            elif board in ("month", "week"):
                for r in (
                    await session.execute(_MONTHS if board == "month" else _WEEKS, params)
                ).all():
                    calendar[r.bucket][1 if r.masked else 0] += r.n

        rate: RateOut | None = None
        if board == "station-rate":
            ids = [sid for sid in sel.names if sel.names[sid][0] in station_buckets]
            denominators = {
                r.org_unit_id: float(r.value)
                for r in await session.execute(
                    _DENOMINATORS, {"units": ids, "from": start, "to": end}
                )
            }
            rows = [
                _row(b, sel.may_mask, counted(b.previous_unmasked, b.previous_masked, sel.may_mask))
                for b in station_buckets.values()
            ]
            by_code = {sel.names[sid][0]: sid for sid in ids}
            for row in rows:
                sid = by_code.get(row.key)
                value = denominators.get(sid) if sid else None
                if value:
                    row.rate = round(row.count / value * 100_000, 1)
                    row.rate_label = "per 100,000 residents in the period"
                else:
                    row.rate_label = RATE_UNAVAILABLE
            all_rates = bool(rows) and all(r.rate is not None for r in rows)
            rate = RateOut(
                available=all_rates,
                label="Ranked by rate per 100,000 residents (approved denominators)."
                if all_rates
                else f"{RATE_UNAVAILABLE}. Ranked by record count instead.",
            )
            ranked = _rank(rows, by_rate=all_rates)
        elif board == "region":
            ranked = _rank(
                (
                    _row(
                        b,
                        sel.may_mask,
                        counted(b.previous_unmasked, b.previous_masked, sel.may_mask),
                    )
                    for b in region_buckets.values()
                ),
                by_rate=False,
            )
        elif board in ("month", "week"):
            ranked = _calendar_rows(calendar, start, end, sel.may_mask, board)
            for i, row in enumerate(sorted(ranked, key=lambda x: -x.count), start=1):
                row.rank = i
        else:
            for b in other.values():
                if not b.label:
                    b.label = f"Cell {b.key}"
            ranked = _rank(
                (
                    _row(
                        b,
                        sel.may_mask,
                        counted(b.previous_unmasked, b.previous_masked, sel.may_mask),
                    )
                    for b in other.values()
                ),
                by_rate=False,
            )
            ranked = [x for x in ranked if x.count > 0]
            for i, row in enumerate(ranked, start=1):
                row.rank = i
        total_rows = len(ranked)
        if board in ("top-spot", "location"):
            ranked = ranked[:TOP_ROWS]
        uow.audit(
            ctx.event(
                "ANALYTICS_QUERY",
                object_type="trend_board",
                object_id=board,
                filters={
                    "region": region,
                    "station": station,
                    "category": category,
                    "period_from": start.isoformat(),
                    "period_to": end.isoformat(),
                    "time_basis": time_basis,
                },
                after_meta={"rows": total_rows, "selected": totals["selected"]},
            )
        )
        freshness = await _freshness(session)
        await uow.commit()

    selected = totals["selected"]
    limitations = [
        "Synthetic demo data and synthetic boundaries; not official jurisdictions.",
        "Records the caller is not authorised to see (including HIGHLY_RESTRICTED) are not counted.",
    ]
    if sel.may_mask:
        limitations.append(
            f"Sensitive categories are combined and counted in a row only where there are at least "
            f"{SMALL_CELL}; smaller numbers are not counted anywhere."
        )
    if time_basis == "OCCURRENCE":
        limitations.append(
            "Records without an occurrence time are not included; choose 'reported' time to include them."
        )
    if board == "top-spot":
        limitations.append("Records without a usable location are not included in cells.")
    if board == "station-rate" and unassigned:
        limitations.append(
            f"{unassigned:,} records without a usable location are not assigned to a station; they "
            "are counted in the region, time, month and week boards only."
        )
    scope_label = (
        sel.names[sel.station_ids[0]][1]
        if sel.station_ids
        else "All regions in your scope"
        if region == "ALL"
        else sel.regions[0].region.name
    )
    return TrendBoardOut(
        data_label=DATA_LABEL,
        board=board,
        title=BOARD_TITLES[board],
        period=DashboardPeriodOut(period_from=start, period_to=end, basis=time_basis),
        category=sel.category_label,
        scope=ScopeOut(region=region, label=scope_label),
        measure=MEASURES[board],
        comparison=COMPARISONS.get(board, DEFAULT_COMPARISON),
        rows=ranked,
        total_rows=total_rows,
        quality=QualityOut(
            selected=selected,
            usable_location_pct=round(totals["usable"] / selected * 100, 1) if selected else None,
            dq_warning_pct=round(totals["dq"] / selected * 100, 1) if selected else None,
            caveat=(
                f"{_period_label(start, end)}: {totals['usable']:,} of {selected:,} records counted "
                f"individually have a usable location; {totals['dq']:,} carry a data-quality warning. "
                "Counts reflect what was recorded, not all that happened."
            ),
        ),
        rate=rate,
        notice=EXPORT_NOTICE,
        limitations=limitations,
        last_refreshed_at=freshness,
    )
