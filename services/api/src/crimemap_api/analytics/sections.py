"""Analytics Workbench sections (Phase 5). Each returns ``(data, meta)`` for the standard envelope
(api.md §2): every result states its filters, record count, excluded records with reasons, data
freshness, method and parameters, caveats and classification.

Privacy (ADR-012) is applied per bucket: records the caller may see only as a combined sensitive
group ("masked") count in a bucket only when there are at least 3 of them there; smaller groups are
counted nowhere and the response says so in the same words whatever their size.
"""

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.analytics import sql
from crimemap_api.analytics.query import AnalyticsQuery, Resolved, Window, resolve_query
from crimemap_api.deps import Ctx
from crimemap_api.http import ProblemError
from crimemap_api.routes.map import _FRESHNESS, SMALL_CELL, SUPPRESSION_LABEL
from crimemap_api.territory import load_region

SENSITIVE = ("SENSITIVE", "Sensitive categories (combined)")
UNMAPPED = ("UNMAPPED", "Unmapped offence code")
KDE_MIN_RECORDS = 200
KDE_MIN_USABLE_PCT = 80.0
KDE_BANDWIDTH_M = 500
KDE_CELL_M = 250
REPEAT_MIN_WEEKS = 3
TOP_CELL_SHARE = 0.10
MAX_CELLS = 3000

TITLES: dict[str, tuple[str, str]] = {
    "overview": (
        "Overview",
        "Headline counts for the selected filters, the change against the comparison period, how many "
        "records have a usable location, and open review items.",
    ),
    "location": (
        "Location Trends",
        "Where records were recorded: counts per grid cell or unit, localities recorded in several "
        "weeks, cells that stand out in both periods, and a density surface when the data allows.",
    ),
    "case_trends": (
        "Case Trends",
        "When records were recorded: trends by category, day of week and hour, by day, the delay "
        "between occurrence and report, and case status where permitted.",
    ),
    "comparison": (
        "Comparison",
        "Two selections side by side (A and B) with counts, percentage change and a difference table.",
    ),
    "data_quality": (
        "Data Quality",
        "How complete and reliable the selected records are: missing or approximate locations, "
        "missing times, open issues and feed health.",
    ),
}


def counted(unmasked: int, masked: int, may_mask: bool) -> int:
    """A bucket (day, week, cell, unit, hour, delay band) counts only the records the caller may see
    individually. Combined sensitive records are never placed in a time or place bucket: otherwise a
    period total minus the bucket sums would reveal small groups (Phase 5 audit). They appear once,
    as the combined total (3 or more) in the headline count and the category list."""
    del masked, may_mask
    return unmasked


def pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def change(a: int, b: int | None) -> float | None:
    return round((a - b) / b * 100, 1) if b else None


async def _categories(session: AsyncSession) -> dict[UUID, tuple[str, str]]:
    return {r.id: (r.code, r.name) for r in await session.execute(sql.CATEGORIES)}


async def freshness(session: AsyncSession) -> str | None:
    value = (await session.execute(_FRESHNESS)).scalar_one_or_none()
    return value.astimezone(UTC).isoformat() if value else None


class Totals:
    """Headline numbers from the TOTALS statement, privacy rule applied once."""

    def __init__(self, rows: Sequence[Any], may_mask: bool) -> None:
        self.unmasked = 0
        self.masked = 0
        self.unmasked_located = 0
        self.dq = 0
        self.by_category: dict[UUID | None, int] = defaultdict(int)
        self.by_quality: dict[str, int] = defaultdict(int)
        self.by_record_type: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.masked:
                self.masked += r.n
                continue
            self.unmasked += r.n
            self.unmasked_located += r.n if r.located else 0
            self.dq += r.n if r.quality != "OK" else 0
            self.by_category[r.category_id] += r.n
            self.by_quality[r.quality] += r.n
            self.by_record_type[r.record_type] += r.n
        self.may_mask = may_mask
        # Applied whenever masked records exist (also for callers whose sensitive access is
        # station-based but who see the region): never 1-2.
        self.combined = self.masked if self.masked >= SMALL_CELL else 0
        self.selected = self.unmasked + self.combined

    @property
    def not_located(self) -> int:
        return self.unmasked - self.unmasked_located


async def totals(session: AsyncSession, r: Resolved, window: Window) -> Totals:
    return Totals((await session.execute(sql.TOTALS, r.args(window))).all(), r.may_mask)


def meta(
    ctx: Ctx,
    r: Resolved,
    section: str,
    t: Totals,
    *,
    refreshed: str | None,
    method: str,
    parameters: dict[str, Any],
    excluded: dict[str, int] | None = None,
    caveats: list[str] | None = None,
) -> dict[str, Any]:
    reasons = {k: v for k, v in (excluded or {}).items() if v}
    title, explanation = TITLES[section]
    regions, stations = r.coverage()
    return {
        # Internal: what the result covers and how it was computed (stored with snapshots, removed
        # before a response is sent).
        "_scope": {
            "section": section,
            "region_ids": [str(x) for x in regions],
            "station_ids": [str(x) for x in stations] if stations is not None else None,
            "may_mask": r.may_mask,
        },
        "request_id": ctx.audit_ctx.request_id,
        "title": title,
        "explanation": explanation,
        "filters_applied": r.filters_applied(),
        "record_count": t.selected,
        "excluded_count": sum(reasons.values()),
        "excluded_reasons": reasons,
        "suppressed": SUPPRESSION_LABEL if r.may_mask else None,
        "data_freshness": refreshed,
        "method": method,
        "method_parameters": parameters,
        "data_quality_note": (
            f"{pct(t.unmasked_located, t.unmasked) or 0}% of the records counted individually have a usable "
            f"location; {t.dq:,} carry a data-quality warning. Counts reflect what was recorded."
        ),
        "caveats": [*r.caveats, *(caveats or [])],
        "classification": "RESTRICTED",
        "environment": ctx.services.settings.environment,
        "evidence_snapshot_id": None,
    }


def _grain(r: Resolved) -> str:
    if r.query.grain:
        return r.query.grain
    days = (r.current.end - r.current.start).days + 1
    return "day" if days <= 62 else "week" if days <= 200 else "month"


async def _series(
    session: AsyncSession,
    r: Resolved,
    window: Window,
    grain: str,
    cats: dict[UUID, tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    """Totals per bucket and per (category, bucket); masked rows form the combined bucket."""
    rows = (await session.execute(sql.SERIES, {**r.args(window), "grain": grain})).all()
    unmasked: dict[date, int] = defaultdict(int)
    masked: dict[date, int] = defaultdict(int)
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        key = row.bucket.isoformat()
        if row.masked:
            masked[row.bucket] += row.n
        else:
            unmasked[row.bucket] += row.n
            code = cats.get(row.category_id, UNMAPPED)[0]
            by_cat[code][key] += row.n
    for bucket, n in masked.items():
        if counted(0, n, r.may_mask):
            by_cat[SENSITIVE[0]][bucket.isoformat()] += n
    buckets = sorted(set(unmasked) | set(masked))
    points = [
        {
            "bucket": b.isoformat(),
            "count": counted(unmasked.get(b, 0), masked.get(b, 0), r.may_mask),
        }
        for b in buckets
    ]
    return points, {k: dict(v) for k, v in by_cat.items()}


def _category_rows(t: Totals, cats: dict[UUID, tuple[str, str]]) -> list[dict[str, Any]]:
    rows = [
        {
            "code": cats[cid][0] if cid in cats else UNMAPPED[0],
            "label": cats[cid][1] if cid in cats else UNMAPPED[1],
            "count": n,
        }
        for cid, n in t.by_category.items()
    ]
    if t.combined:
        rows.append({"code": SENSITIVE[0], "label": SENSITIVE[1], "count": t.combined})
    return sorted(rows, key=lambda x: (-x["count"], x["label"]))


async def _cells(
    session: AsyncSession, r: Resolved, window: Window, size: int
) -> dict[tuple[int, int], int]:
    rows = (await session.execute(sql.CELLS, {**r.args(window), "size": size})).all()
    unmasked: dict[tuple[int, int], int] = defaultdict(int)
    masked: dict[tuple[int, int], int] = defaultdict(int)
    for row in rows:
        (masked if row.masked else unmasked)[(row.cell_q, row.cell_r)] += row.n
    out = {}
    for key in set(unmasked) | set(masked):
        n = counted(unmasked.get(key, 0), masked.get(key, 0), r.may_mask)
        if n:
            out[key] = n
    return out


async def _hex_features(
    session: AsyncSession, r: Resolved, cells: dict[tuple[int, int], dict[str, Any]], size: int
) -> list[dict[str, Any]]:
    if not cells:
        return []
    keys = sorted(cells, key=lambda k: -cells[k]["count"])[:MAX_CELLS]
    qs, rs = zip(*keys, strict=True)
    shapes = {
        (p.q, p.r): p.geometry
        for p in await session.execute(
            sql.HEX_POLYGONS, {"qs": list(qs), "rs": list(rs), "size": size, "crs": r.units.crs}
        )
    }
    return [
        {
            "type": "Feature",
            "geometry": json.loads(shapes[k]),
            "properties": {
                "kind": "cell",
                "key": f"{r.units.region.code}:{size}:{k[0]}:{k[1]}",
                **cells[k],
            },
        }
        for k in keys
    ]


# --- Overview ------------------------------------------------------------------------------------


async def overview(
    ctx: Ctx, session: AsyncSession, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await resolve_query(ctx, session, q, "overview")
    cats = await _categories(session)
    t = await totals(session, r, r.current)
    prev = await totals(session, r, r.comparison) if r.comparison else None
    grain = "week" if (r.current.end - r.current.start).days <= 200 else "month"
    series, _ = await _series(session, r, r.current, grain, cats)
    cells = await _cells(session, r, r.current, 1000)
    features = await _hex_features(session, r, {k: {"count": v} for k, v in cells.items()}, 1000)
    issues = (await session.execute(sql.OPEN_ISSUES, r.args(r.current))).all()
    by_issue: dict[tuple[str, str], int] = defaultdict(int)
    for row in issues:
        if not row.masked:
            by_issue[(row.issue_type, row.severity)] += row.n
    reviews = (
        await session.execute(
            sql.PATTERN_REVIEWS_OPEN,
            {
                "region": r.units.region.id,
                "stations": r.station_ids,
                "p_start": r.current.start,
                "p_end": r.current.end,
            },
        )
    ).scalar_one()
    refreshed = await freshness(session)
    data = {
        "kpis": {
            "selected": t.selected,
            "comparison_selected": prev.selected if prev else None,
            "change_pct": change(t.selected, prev.selected if prev else None),
            "usable_location_pct": pct(t.unmasked_located, t.unmasked),
            "open_review_items": sum(by_issue.values()) + int(reviews),
        },
        "trend": {"grain": grain, "points": series},
        "categories": _category_rows(t, cats),
        "quality": [{"status": k, "count": v} for k, v in sorted(t.by_quality.items())],
        "map": {"type": "FeatureCollection", "features": features, "hex_size_m": 1000},
        "open_reviews": [
            {"issue_type": k[0], "severity": k[1], "count": v}
            for k, v in sorted(by_issue.items(), key=lambda kv: -kv[1])[:10]
        ]
        + (
            [{"issue_type": "PATTERN_REVIEW_DRAFT", "severity": "-", "count": int(reviews)}]
            if reviews
            else []
        ),
        "review_queue_url": "/review",
    }
    return data, meta(
        ctx,
        r,
        "overview",
        t,
        refreshed=refreshed,
        method="counts_by_category_time_and_1000m_hexagon",
        parameters={"trend_grain": grain, "hex_size_m": 1000, "crs": f"EPSG:{r.units.crs}"},
        excluded={"no_usable_location_on_map": t.not_located},
    )


# --- Location Trends --------------------------------------------------------------------------------


def _kde(cells: dict[tuple[int, int], int]) -> list[tuple[tuple[int, int], float]]:
    """Gaussian kernel density on the 250 m hexagon grid (bandwidth 500 m, cut at 3 bandwidths).

    Density is in records per km² for the period. Pure Python over occupied cells and their
    neighbours within the cut-off, deterministic and documented in method_parameters.
    """
    size = KDE_CELL_M
    h = KDE_BANDWIDTH_M
    reach = math.ceil(3 * h / (math.sqrt(3) * size))
    norm = 1 / (2 * math.pi * (h / 1000) ** 2)  # per km²
    density: dict[tuple[int, int], float] = defaultdict(float)
    for (q, r), n in cells.items():
        for dq in range(-reach, reach + 1):
            for dr in range(max(-reach, -dq - reach), min(reach, -dq + reach) + 1):
                # centre distance between axial cells (pointy-top): sqrt(3)*size*sqrt(dq²+dq*dr+dr²)
                d = math.sqrt(3) * size * math.sqrt(dq * dq + dq * dr + dr * dr)
                if d <= 3 * h:
                    density[(q + dq, r + dr)] += n * norm * math.exp(-(d * d) / (2 * h * h))
    top = max(density.values(), default=0)
    return sorted(
        ((k, round(v, 2)) for k, v in density.items() if v >= 0.05 * top), key=lambda kv: -kv[1]
    )


async def location(
    ctx: Ctx, session: AsyncSession, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await resolve_query(ctx, session, q, "location")
    t = await totals(session, r, r.current)
    size = int(q.hex_size_m)
    data: dict[str, Any] = {"unit": q.unit}
    if q.unit == "HEX":
        current = await _cells(session, r, r.current, size)
        previous = await _cells(session, r, r.comparison, size) if r.comparison else {}
        cells = {
            k: {"count": v, "comparison_count": previous.get(k, 0) if r.comparison else None}
            for k, v in current.items()
        }
        data["map"] = {
            "type": "FeatureCollection",
            "features": await _hex_features(session, r, cells, size),
            "hex_size_m": size,
        }

        # Persistence: cells in the top 10% (and at least 3 records) in each period.
        def top(cs: dict[tuple[int, int], int]) -> set[tuple[int, int]]:
            ranked = sorted((v for v in cs.values() if v >= SMALL_CELL), reverse=True)
            if not ranked:
                return set()
            cut = ranked[max(0, math.ceil(len(ranked) * TOP_CELL_SHARE) - 1)]
            return {k for k, v in cs.items() if v >= max(cut, SMALL_CELL)}

        a, b = top(current), top(previous)
        rc = r.units.region.code
        data["persistence"] = (
            sorted(
                (
                    {
                        "key": f"{rc}:{size}:{k[0]}:{k[1]}",
                        "status": "IN_BOTH"
                        if k in a and k in b
                        else "CURRENT_ONLY"
                        if k in a
                        else "COMPARISON_ONLY",
                        "count": current.get(k, 0),
                        "comparison_count": previous.get(k, 0),
                    }
                    for k in a | b
                ),
                key=lambda x: (-x["count"], x["key"]),
            )
            if r.comparison
            else []
        )
    else:
        rows = (await session.execute(sql.UNITS, r.args(r.current))).all()
        unmasked: dict[UUID, int] = defaultdict(int)
        masked: dict[UUID, int] = defaultdict(int)
        for row in rows:
            unit_id = row.beat_id if q.unit == "BEAT" else row.station_id
            if unit_id is None:
                continue
            (masked if row.masked else unmasked)[unit_id] += row.n
        counts = {
            u: counted(unmasked.get(u, 0), masked.get(u, 0), r.may_mask)
            for u in set(unmasked) | set(masked)
        }
        shapes = (
            (await session.execute(sql.UNIT_SHAPES, {"ids": list(counts)})).all() if counts else []
        )
        data["map"] = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": json.loads(s.geometry),
                    "properties": {
                        "kind": q.unit.lower(),
                        "code": s.code,
                        "name": s.name,
                        "count": counts[s.unit_id],
                    },
                }
                for s in sorted(shapes, key=lambda s: s.code)
                if counts.get(s.unit_id)
            ],
        }
        data["persistence"] = []
    # Repeat locations: localities with records in at least REPEAT_MIN_WEEKS distinct weeks.
    names = {s.id: s.code for s in r.units.stations.values()}
    repeat = []
    for row in (await session.execute(sql.REPEAT, r.args(r.current))).all():
        if row.masked or row.weeks < REPEAT_MIN_WEEKS:
            continue  # sensitive localities are never listed individually
        repeat.append(
            {
                "locality": row.locality,
                "station": names.get(row.station_id),
                "weeks": row.weeks,
                "count": row.n,
            }
        )
    data["repeat_locations"] = sorted(
        repeat, key=lambda x: (-x["weeks"], -x["count"], x["locality"])
    )[:25]
    # Density only when there is enough, good-enough data (configured thresholds).
    usable = pct(t.unmasked_located, t.unmasked) or 0
    if t.selected >= KDE_MIN_RECORDS and usable >= KDE_MIN_USABLE_PCT:
        fine = await _cells(session, r, r.current, KDE_CELL_M)
        dens = _kde(fine)[:MAX_CELLS]
        data["kde"] = {
            "available": True,
            "reason": None,
            "map": {
                "type": "FeatureCollection",
                "features": await _hex_features(
                    session, r, {k: {"density_per_km2": v, "count": 0} for k, v in dens}, KDE_CELL_M
                ),
            },
        }
    else:
        data["kde"] = {
            "available": False,
            "reason": f"Density needs at least {KDE_MIN_RECORDS} records and {KDE_MIN_USABLE_PCT:.0f}% usable locations "
            f"(this selection: {t.selected:,} records, {usable}%).",
            "map": None,
        }
    data["road_segments"] = {
        "available": False,
        "reason": "No road-segment association exists in the demo data, so a corridor view cannot be drawn.",
    }
    refreshed = await freshness(session)
    return data, meta(
        ctx,
        r,
        "location",
        t,
        refreshed=refreshed,
        method="hex_grid_count" if q.unit == "HEX" else f"{q.unit.lower()}_choropleth_count",
        parameters={
            "spatial_unit": q.unit,
            "cell_size_m": size if q.unit == "HEX" else None,
            "crs": f"EPSG:{r.units.crs}",
            "repeat_location_min_weeks": REPEAT_MIN_WEEKS,
            "persistence": f"top {int(TOP_CELL_SHARE * 100)}% of cells with at least {SMALL_CELL} records in each period",
            "kde": f"Gaussian, bandwidth {KDE_BANDWIDTH_M} m on {KDE_CELL_M} m hexagons, records per km²",
        },
        excluded={"no_usable_location": t.not_located},
    )


# --- Case Trends ------------------------------------------------------------------------------------


async def case_trends(
    ctx: Ctx, session: AsyncSession, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await resolve_query(ctx, session, q, "case_trends")
    cats = await _categories(session)
    t = await totals(session, r, r.current)
    grain = _grain(r)
    series, by_category = await _series(session, r, r.current, grain, cats)
    day, _ = await _series(session, r, r.current, "day", cats)
    grid: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for row in (await session.execute(sql.DOW_HOUR, r.args(r.current))).all():
        grid[(row.dow, row.hour)][1 if row.masked else 0] += row.n
    heat = [
        [
            counted(grid.get((d, h), [0, 0])[0], grid.get((d, h), [0, 0])[1], r.may_mask)
            for h in range(24)
        ]
        for d in range(1, 8)
    ]
    delay: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in (await session.execute(sql.DELAY, r.args(r.current))).all():
        delay[row.bucket][1 if row.masked else 0] += row.n
    order = [
        "same_day",
        "1_2_days",
        "3_7_days",
        "8_30_days",
        "over_30_days",
        "reported_before_occurrence",
        "unknown",
    ]
    status: dict[str, Any]
    if q.record_type != "CASE":
        status = {
            "available": False,
            "reason": "Case status applies to case records only; choose the CASE record type.",
        }
    elif not r.may_see_status:
        status = {
            "available": False,
            "reason": "Case status needs record-summary access for this region.",
        }
    else:
        rows = (await session.execute(sql.STATUS, r.args(r.current))).all()
        groups = {
            "REGISTERED": "active",
            "UNDER_INVESTIGATION": "active",
            "DETECTED": "detected",
            "CLOSED": "closed",
        }
        months: dict[str, dict[str, int]] = defaultdict(
            lambda: {"active": 0, "detected": 0, "closed": 0}
        )
        for row in rows:
            if row.masked:
                continue  # sensitive-category cases are not in status counts for combined callers
            g = groups.get(row.status_code)
            if g:
                months[row.bucket.isoformat()][g] += row.n
        status = {
            "available": True,
            "definition": "Active = registered or under investigation; detected; closed (status definitions, CASE records).",
            "points": [{"bucket": k, **v} for k, v in sorted(months.items())],
        }
    labels = {c[0]: c[1] for c in cats.values()} | {
        SENSITIVE[0]: SENSITIVE[1],
        UNMAPPED[0]: UNMAPPED[1],
    }
    data = {
        "grain": grain,
        "series": series,
        "by_category": [
            {
                "code": code,
                "label": labels.get(code, code),
                "points": [{"bucket": b, "count": n} for b, n in sorted(points.items())],
            }
            for code, points in sorted(by_category.items(), key=lambda kv: -sum(kv[1].values()))
        ],
        "day_hour": {
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "hours": list(range(24)),
            "counts": heat,
        },
        "calendar": day,
        "delay": [
            {
                "bucket": b,
                "count": counted(delay.get(b, [0, 0])[0], delay.get(b, [0, 0])[1], r.may_mask),
            }
            for b in order
        ],
        "status": status,
    }
    refreshed = await freshness(session)
    return data, meta(
        ctx,
        r,
        "case_trends",
        t,
        refreshed=refreshed,
        method="time_bucket_counts",
        parameters={"grain": grain, "time_zone": "Asia/Kolkata", "week_start": "Monday"},
        caveats=[
            "Delay compares the recorded occurrence time with the report time; it is not a response time."
        ],
    )


# --- Comparison -------------------------------------------------------------------------------------


async def comparison(
    ctx: Ctx, session: AsyncSession, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    if q.compare is None:
        raise ProblemError(422, "validation-error", "Choose what to compare (compare, a, b)")
    base = await resolve_query(
        ctx, session, q.model_copy(update={"comparison": "NONE"}), "comparison", "analytics.compare"
    )
    cats = await _categories(session)
    sides: list[tuple[str, Resolved, Window]] = []
    kind = q.compare
    if kind in ("PERIOD", "YEAR"):
        from crimemap_api.analytics.query import comparison_window

        other = comparison_window(base.current, "PREVIOUS" if kind == "PERIOD" else "YEAR")
        if other is None:  # PREVIOUS/YEAR always give a window; kept explicit for -O
            raise ProblemError(422, "validation-error", "No comparison period")
        sides = [
            (f"{base.current.start} to {base.current.end}", base, base.current),
            (f"{other.start} to {other.end}", base, other),
        ]
    elif kind == "CATEGORY":
        if q.a and q.a == q.b:
            raise ProblemError(422, "validation-error", "A and B must differ")
        if not (q.a and q.b):
            raise ProblemError(422, "validation-error", "Give two categories (a and b)")
        for code in (q.a, q.b):
            rr = await resolve_query(
                ctx,
                session,
                q.model_copy(update={"category": [code], "comparison": "NONE"}),
                "comparison",
                "analytics.compare",
            )
            sides.append((rr.category_label, rr, rr.current))
    else:
        if q.a and q.a == q.b:
            raise ProblemError(422, "validation-error", "A and B must differ")
        if not (q.a and q.b):
            raise ProblemError(422, "validation-error", "Give two units (a and b)")
        for code in (q.a, q.b):
            region = code if kind == "REGION" else code.split("-")[0]
            update: dict[str, Any] = {
                "region": region,
                "station": [],
                "beat": [],
                "comparison": "NONE",
            }
            if kind == "STATION":
                update["station"] = [code]
            elif kind == "BEAT":
                update["beat"] = [code]
            rr = await resolve_query(
                ctx, session, q.model_copy(update=update), "comparison", "analytics.compare"
            )
            units = await load_region(session, region)
            if units is None:  # resolve_query already refused unknown regions
                raise ProblemError(422, "validation-error", "Unknown region")
            unit = units.stations.get(code) or units.beats.get(code)
            name = units.region.name if kind == "REGION" or unit is None else unit.name
            sides.append((name, rr, rr.current))
    grain = _grain(base)
    out: list[dict[str, Any]] = []
    side_totals: list[Totals] = []
    for label, rr, window in sides:
        t = await totals(session, rr, window)
        side_totals.append(t)
        series, _ = await _series(session, rr, window, grain, cats)
        cells = await _cells(session, rr, window, 1000)
        out.append(
            {
                "label": label,
                "region": rr.units.region.code,
                "count": t.selected,
                "usable_location_pct": pct(t.unmasked_located, t.unmasked),
                "series": series,
                "categories": _category_rows(t, cats),
                "map": {
                    "type": "FeatureCollection",
                    "features": await _hex_features(
                        session, rr, {k: {"count": v} for k, v in cells.items()}, 1000
                    ),
                    "hex_size_m": 1000,
                },
            }
        )
    a_cats = {c["code"]: c for c in out[0]["categories"]}
    b_cats = {c["code"]: c for c in out[1]["categories"]}
    table = []
    for code in sorted(set(a_cats) | set(b_cats)):
        av = a_cats.get(code, {}).get("count", 0)
        bv = b_cats.get(code, {}).get("count", 0)
        table.append(
            {
                "code": code,
                "label": (a_cats.get(code) or b_cats.get(code) or {}).get("label", code),
                "a": av,
                "b": bv,
                "difference": av - bv,
                "change_pct": change(av, bv),
            }
        )
    table.sort(key=lambda x: -abs(x["difference"]))
    caveats = [
        "Rates are not shown: no approved population denominator exists for these units (ADR-014)."
    ]
    ua, ub = out[0]["usable_location_pct"] or 0, out[1]["usable_location_pct"] or 0
    if abs(ua - ub) >= 10:
        caveats.append(
            f"Data quality differs between A and B (usable locations {ua}% vs {ub}%); compare with care."
        )
    days = [(w.end - w.start).days + 1 for _, _, w in sides]
    if days[0] != days[1]:
        caveats.append("A and B cover periods of different length.")
    data = {
        "kind": kind,
        "a": out[0],
        "b": out[1],
        "difference": {
            "a": out[0]["count"],
            "b": out[1]["count"],
            "difference": out[0]["count"] - out[1]["count"],
            "change_pct": change(out[0]["count"], out[1]["count"]),
        },
        "table": table,
        "rate": {"available": False, "label": "Rate unavailable — no approved denominator"},
    }
    refreshed = await freshness(session)
    m = meta(
        ctx,
        base,
        "comparison",
        side_totals[0],
        refreshed=refreshed,
        method="a_vs_b_counts",
        parameters={"kind": kind, "grain": grain, "hex_size_m": 1000},
        caveats=caveats,
    )
    m["record_count"] = side_totals[0].selected + side_totals[1].selected
    # A comparison covers both sides: a reader must be allowed both.
    regions: list[str] = []
    stations: list[str] | None = []
    for _label, rr, _w in sides:
        reg, sts = rr.coverage()
        regions += [str(x) for x in reg if str(x) not in regions]
        stations = (
            None
            if (stations is None or sts is None)
            else [*stations, *(str(x) for x in sts if str(x) not in stations)]
        )
    m["_scope"] = {
        "section": "comparison",
        "region_ids": regions,
        "station_ids": stations,
        "may_mask": any(rr.may_mask for _, rr, _ in sides),
    }
    m["filters_applied"]["compare"] = {"kind": kind, "a": out[0]["label"], "b": out[1]["label"]}
    return data, m


# --- Data Quality -----------------------------------------------------------------------------------


async def data_quality(
    ctx: Ctx, session: AsyncSession, q: AnalyticsQuery
) -> tuple[dict[str, Any], dict[str, Any]]:
    r = await resolve_query(ctx, session, q, "data_quality")
    t = await totals(session, r, r.current)
    dq_rows = (await session.execute(sql.DQ, r.args(r.current))).all()
    method: dict[str, int] = defaultdict(int)
    confidence: dict[str, int] = defaultdict(int)
    precision: dict[str, int] = defaultdict(int)
    no_location = outside = 0
    for row in dq_rows:
        if row.masked:
            continue  # the DQ view covers records counted individually
        method[row.geocode_method] += row.n
        confidence[row.confidence_band] += row.n
        precision[row.precision_level] += row.n
        no_location += row.n if row.no_location else 0
        outside += row.n if row.outside else 0
    # Measured on report time: such records have no occurrence time to select them by.
    missing_occ = sum(
        row.n
        for row in (
            await session.execute(sql.MISSING_OCCURRENCE, r.args(r.current, basis="REPORTED"))
        ).all()
        if not row.masked
    )
    issues: dict[str, int] = defaultdict(int)
    for row in (await session.execute(sql.OPEN_ISSUES, r.args(r.current))).all():
        if not row.masked:
            issues[row.issue_type] += row.n
    feeds = [
        {
            "code": f.code,
            "name": f.name,
            "approved_status": f.approved_status,
            "last_batch_at": f.last_batch_at.astimezone(UTC).isoformat()
            if f.last_batch_at
            else None,
            "health": f.last_health_status,
            "stale_after_hours": f.stale_after_hours,
            "open_dead_letters": int(f.open_dead_letters or 0),
        }
        for f in await session.execute(sql.FEEDS)
    ]
    n = t.unmasked
    low = confidence.get("low", 0) + confidence.get("none", 0) - no_location
    data = {
        "counted_individually": n,
        "measures": [
            {
                "code": "missing_location",
                "label": "Missing location",
                "count": no_location,
                "pct": pct(no_location, n),
            },
            {
                "code": "outside_jurisdiction",
                "label": "Location outside every synthetic beat",
                "count": outside,
                "pct": pct(outside, n),
            },
            {
                "code": "low_confidence",
                "label": "Approximate or low-confidence location (confidence below 0.4)",
                "count": max(low, 0),
                "pct": pct(max(low, 0), n),
            },
            {
                "code": "missing_occurrence_time",
                "label": "Missing occurrence time (records in the period by report time)",
                "count": missing_occ,
                "pct": None,
            },
            {
                "code": "possible_duplicates",
                "label": "Possible duplicates (open issues)",
                "count": issues.get("POSSIBLE_DUPLICATE", 0),
                "pct": pct(issues.get("POSSIBLE_DUPLICATE", 0), n),
            },
            {
                "code": "invalid_mapping",
                "label": "Invalid category/section mapping (open issues)",
                "count": issues.get("INVALID_CATEGORY_MAPPING", 0),
                "pct": pct(issues.get("INVALID_CATEGORY_MAPPING", 0), n),
            },
            {
                "code": "stale_sources",
                "label": "Late or stale source feeds",
                "count": sum(1 for f in feeds if f["health"] == "STALE"),
                "pct": None,
            },
        ],
        "geocode_methods": [
            {"method": k, "count": v} for k, v in sorted(method.items(), key=lambda kv: -kv[1])
        ],
        "confidence": [
            {"band": b, "count": confidence.get(b, 0)} for b in ("high", "medium", "low", "none")
        ],
        "precision": [{"level": k, "count": v} for k, v in sorted(precision.items())],
        "open_issues": [
            {"issue_type": k, "count": v} for k, v in sorted(issues.items(), key=lambda kv: -kv[1])
        ],
        "feeds": feeds,
        "review_queue_url": "/review",
    }
    refreshed = await freshness(session)
    return data, meta(
        ctx,
        r,
        "data_quality",
        t,
        refreshed=refreshed,
        method="completeness_and_issue_counts",
        parameters={"low_confidence_below": 0.4, "high_confidence_from": 0.8},
        caveats=[
            "Sensitive-category records are not broken down here for callers who see them only combined."
        ]
        if r.may_mask
        else None,
    )


SECTIONS = {
    "overview": overview,
    "location": location,
    "case_trends": case_trends,
    "comparison": comparison,
    "data_quality": data_quality,
}
