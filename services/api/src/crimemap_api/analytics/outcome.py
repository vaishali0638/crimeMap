"""Outcome review metrics (Phase 6): recorded counts before and after an action, in the action's
cells, in the ring of cells around them, and in a comparison area — computed by the server from the
same analytics selection (ADR-032/035), with the caller's own access and the ADR-012 rule.

The query is the pattern review's own evidence query (category, record type, time basis, hours,
locality, confidence, status), with only the period replaced, so the counts measure what the review
measured (Phase 6 audit). Station and beat filters are dropped: the area is defined by the cells.

These are descriptive counts, not an evaluation. The ring helps a reviewer think about displacement
(records moving next door) or diffusion of benefit (falls next door too); the numbers alone show
neither, and the response says so.

Each result records what it covers (``_coverage``: regions, stations, whether sensitive categories
were combined), so a later reader is shown the numbers only with the access that produced them.
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.analytics.query import AnalyticsQuery, _window, resolve_query, section_scope
from crimemap_api.analytics.sections import _cells, totals
from crimemap_api.deps import Ctx
from crimemap_api.policy.engine import PermissionScope, permission_scope

NEIGHBOURS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))  # axial hexagon neighbours

# Evidence parameters that define *which records* count; period, area and view are set here.
_KEPT = (
    "category",
    "locality",
    "record_type",
    "status",
    "min_confidence",
    "time_basis",
    "hour_from",
    "hour_to",
)


def parse_keys(keys: list[str]) -> tuple[str, int, set[tuple[int, int]]]:
    """Cell keys "REG:size:q:r" -> region, size, cells (all keys must share region and size)."""
    parts = [k.split(":") for k in keys]
    region, size = parts[0][0], int(parts[0][1])
    if any(p[0] != region or int(p[1]) != size for p in parts):
        raise ValueError("cells must share one region and grid size")
    return region, size, {(int(p[2]), int(p[3])) for p in parts}


def ring(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """The cells touching the area but not in it (one hexagon ring)."""
    return {(q + dq, r + dr) for q, r in cells for dq, dr in NEIGHBOURS} - cells


def _per_30(count: int, days: int) -> float:
    return round(count / days * 30, 2) if days else 0.0


def evidence_query(params: dict[str, Any], region: str, start: date, end: date) -> AnalyticsQuery:
    """The review's evidence query for another period (no comparison, whole region)."""
    kept = {k: params[k] for k in _KEPT if k in params}
    return AnalyticsQuery.model_validate(
        {**kept, "region": region, "period_from": start, "period_to": end, "comparison": "NONE"}
    )


def coverage(
    region_ids: list[UUID], station_ids: list[UUID] | None, may_mask: bool
) -> dict[str, Any]:
    return {
        "region_ids": sorted(str(r) for r in region_ids),
        "station_ids": None if station_ids is None else sorted(str(s) for s in station_ids),
        "may_mask": may_mask,
    }


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Both coverages: region-wide (``station_ids`` None) if either is; otherwise both station sets."""
    stations = (
        None
        if a["station_ids"] is None or b["station_ids"] is None
        else sorted(set(a["station_ids"]) | set(b["station_ids"]))
    )
    return {
        "region_ids": sorted(set(a["region_ids"]) | set(b["region_ids"])),
        "station_ids": stations,
        "may_mask": bool(a["may_mask"]) and bool(b["may_mask"]),
    }


async def measure(
    ctx: Ctx,
    session: AsyncSession,
    *,
    params: dict[str, Any],
    action_keys: list[str],
    start: date,
    end: date,
    comparison_kind: str,
    comparison_keys: list[str],
) -> dict[str, Any]:
    region, size, area = parse_keys(action_keys)
    q = evidence_query(params, region, start, end).model_copy(update={"hex_size_m": str(size)})
    r = await resolve_query(ctx, session, q, "location")
    window = _window(start, end)
    counts = await _cells(session, r, window, size)
    days = (end - start).days + 1
    covered = coverage(*r.coverage(), r.may_mask)
    out: dict[str, Any] = {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "category": r.category_label,
        "time_basis": q.time_basis.lower(),
        "record_type": q.record_type,
        "filters": r.filters_applied(),
        "grid": {"size_m": size, "cells": len(area)},
        "action_area": sum(counts.get(c, 0) for c in area),
        "surrounding_ring": sum(counts.get(c, 0) for c in ring(area)),
        "comparison_area": None,
    }
    if comparison_kind == "CELLS" and comparison_keys:
        cmp_region, csize, cmp_cells = parse_keys(comparison_keys)
        if cmp_region != region:
            raise ValueError("comparison cells must be in the action's region")
        cmp_counts = counts if csize == size else await _cells(session, r, window, csize)
        out["comparison_area"] = sum(cmp_counts.get(c, 0) for c in cmp_cells)
    elif comparison_kind == "STATION" and comparison_keys:
        rs = await resolve_query(
            ctx, session, q.model_copy(update={"station": comparison_keys[:1]}), "location"
        )
        out["comparison_area"] = (await totals(session, rs, window)).selected
        covered = merge(covered, coverage(*rs.coverage(), rs.may_mask))
    out["per_30_days"] = {
        k: _per_30(out[k], days)
        for k in ("action_area", "surrounding_ring", "comparison_area")
        if out[k] is not None
    }
    out["sensitive_rule"] = (
        "Sensitive categories are combined and counted only when at least 3; they are not in cell counts."
        if r.may_mask
        else None
    )
    out["_coverage"] = covered
    return out


def may_read_metrics(ctx: Ctx, metrics: dict[str, Any], author: UUID, region_id: UUID) -> bool:
    """The reviewer, or a reader with analytics access over every unit the counts cover and, when
    they were computed without combining sensitive categories, sensitive access over them too (the
    evidence snapshot rule, ADR-035)."""
    if author == ctx.user.id:
        return True
    cov = metrics.get("_coverage") or {
        # Rows from before the Phase 6 audit: the whole region; combined only if the rule was applied.
        "region_ids": [str(region_id)],
        "station_ids": None,
        "may_mask": metrics.get("sensitive_rule") is not None,
    }
    regions = {UUID(x) for x in cov["region_ids"]}
    stations = None if cov["station_ids"] is None else {UUID(x) for x in cov["station_ids"]}

    def covers(s: PermissionScope) -> bool:
        if s.is_global or regions <= s.region_ids:
            return True
        return stations is not None and stations <= s.station_ids

    scope = section_scope(ctx, "location", "analytics.read")
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    return covers(scope) and (bool(cov["may_mask"]) or covers(sensitive))


def public(metrics: dict[str, Any]) -> dict[str, Any]:
    """Metrics without internal fields (coverage)."""
    return {k: v for k, v in metrics.items() if not k.startswith("_")}


def change_summary(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Neutral, descriptive sentences for the reviewer (no causal claim)."""

    def pct(a: float, b: float) -> str:
        return "no baseline" if not b else f"{round((a - b) / b * 100, 1)}%"

    lines = []
    for key, label in (
        ("action_area", "Action area"),
        ("surrounding_ring", "Surrounding ring"),
        ("comparison_area", "Comparison area"),
    ):
        b, a = before["per_30_days"].get(key), after["per_30_days"].get(key)
        if b is None or a is None:
            continue
        lines.append(f"{label}: {b} -> {a} records per 30 days ({pct(a, b)}).")
    lines.append(
        "These are recorded counts only. A change in the ring next to the action area is a prompt to "
        "consider displacement or diffusion of benefit, not evidence of either."
    )
    return lines
