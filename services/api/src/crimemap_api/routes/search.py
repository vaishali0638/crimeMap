"""Global search (Phase 3): permitted record references, units, and localities, all in scope.

- Record references (prefix match) need ``incident.summary.read`` for the record's territory;
  sensitive-category records only with ``incident.sensitive_category.read`` (they are
  aggregate-only otherwise). Row-level security applies as well.
- Regions, stations and beats are INTERNAL reference data (any signed-in user).
- Localities come only from records the caller may see on the map.
  Sensitive-category records are left out unless the caller holds that access (the same rule as
  the map), so a locality count cannot be compared with the map to reveal them.
- Approved categories (sensitive ones only with access) and dates ("2026-07", "2026-07-15") are
  offered as filters for the map.
- Landmarks and road segments are not in the demo data model yet; the response says so rather
  than returning an empty list that looks like "no match".
Every search is audited; a query that looks like a personal identifier (phone, Aadhaar-like,
PAN-like, vehicle registration, email) is recorded as redacted. Personal names cannot be detected
reliably; search never matches names (there are none in the analytical store), but a typed name
is recorded as typed.
"""

import calendar
import re
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from crimemap_api.deps import AUTHENTICATED, Ctx
from crimemap_api.policy.engine import PermissionScope, permission_scope

router = APIRouter(prefix="/api/v1", tags=["search"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]

PERSONAL_PATTERNS = (
    # Indian mobile numbers, with or without +91/0 and spaces or dashes inside
    re.compile(r"(?<!\d)(?:\+?91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\d)"),
    # Aadhaar-like: 12 digits, optionally in groups of four
    re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)"),
    re.compile(r"\b[A-Za-z]{5}\d{4}[A-Za-z]\b"),  # PAN-like
    # vehicle registration, any state code: KA01AB1234, PY 01 C 1234, TN-09-1234
    re.compile(r"\b[A-Za-z]{2}[ -]?\d{1,2}[ -]?[A-Za-z]{0,3}[ -]?\d{4}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
)


def looks_personal(q: str) -> bool:
    return any(p.search(q) for p in PERSONAL_PATTERNS)


class IncidentHit(BaseModel):
    id: UUID
    reference: str
    station_code: str | None
    region_code: str | None


class UnitHit(BaseModel):
    code: str
    name: str
    unit_type: str
    region_code: str


class LocalityHit(BaseModel):
    locality: str
    station_code: str | None
    records: int


class CategoryHit(BaseModel):
    code: str
    label: str


class PeriodHit(BaseModel):
    period_from: date
    period_to: date


class SearchOut(BaseModel):
    query: str
    incidents: list[IncidentHit]
    units: list[UnitHit]
    localities: list[LocalityHit]
    categories: list[CategoryHit]
    period: PeriodHit | None
    not_available: list[str]


_DATE = re.compile(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$")


def parse_period(q: str) -> PeriodHit | None:
    """'2026-07' -> that month; '2026-07-15' -> that day (IST dates, as the map filters use)."""
    m = _DATE.match(q)
    if not m:
        return None
    year, month = int(m[1]), int(m[2])
    try:
        if m[3]:
            day = date(year, month, int(m[3]))
            return PeriodHit(period_from=day, period_to=day)
        last = calendar.monthrange(year, month)[1]
        return PeriodHit(period_from=date(year, month, 1), period_to=date(year, month, last))
    except ValueError:
        return None


def _scope(scope: PermissionScope) -> dict[str, object]:
    return {"g": scope.is_global, "r": list(scope.region_ids), "s": list(scope.station_ids)}


_INCIDENTS = text(
    """
    SELECT i.id, i.source_record_reference, st.code AS station_code, r.code AS region_code
    FROM core.incident_record i
    LEFT JOIN core.organizational_unit st ON st.id = i.station_id
    LEFT JOIN core.organizational_unit r ON r.id = i.region_id
    WHERE i.source_record_reference LIKE :prefix
      AND (:g OR i.region_id = ANY (CAST(:r AS uuid[])) OR i.station_id = ANY (CAST(:s AS uuid[])))
      AND (NOT i.is_sensitive_category OR :sg OR i.region_id = ANY (CAST(:sr AS uuid[]))
           OR i.station_id = ANY (CAST(:ss AS uuid[])))
    ORDER BY i.source_record_reference
    LIMIT 10
    """
)

_UNITS = text(
    """
    SELECT o.code, o.name, o.unit_type,
           coalesce(CASE o.unit_type WHEN 'REGION' THEN o.code WHEN 'STATION' THEN p1.code ELSE p2.code END, '') AS region_code
    FROM core.organizational_unit o
    LEFT JOIN core.organizational_unit p1 ON p1.id = o.parent_id
    LEFT JOIN core.organizational_unit p2 ON p2.id = p1.parent_id
    WHERE o.unit_type IN ('REGION', 'STATION', 'BEAT')
      AND (o.code ILIKE :contains OR o.name ILIKE :contains)
    ORDER BY CASE o.unit_type WHEN 'REGION' THEN 0 WHEN 'STATION' THEN 1 ELSE 2 END, o.code
    LIMIT 10
    """
)

_LOCALITIES = text(
    """
    SELECT l.locality, st.code AS station_code, count(*) AS records
    FROM core.incident_location l
    JOIN core.incident_record i ON i.id = l.incident_id
    LEFT JOIN core.organizational_unit st ON st.id = i.station_id
    WHERE l.locality ILIKE :contains
      AND (:g OR i.region_id = ANY (CAST(:r AS uuid[])) OR i.station_id = ANY (CAST(:s AS uuid[])))
      AND (NOT i.is_sensitive_category OR :sg OR i.region_id = ANY (CAST(:sr AS uuid[]))
           OR i.station_id = ANY (CAST(:ss AS uuid[])))
    GROUP BY 1, 2
    ORDER BY 3 DESC, 1
    LIMIT 10
    """
)


_CATEGORIES = text(
    """
    SELECT code, name FROM core.analytical_category
    WHERE name ILIKE :contains AND (NOT is_sensitive OR :sensitive)
    ORDER BY name LIMIT 5
    """
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/search")
async def search(
    ctx: AuthenticatedCtx,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> SearchOut:
    query = q.strip()
    summary = permission_scope(ctx.subject, "incident.summary.read")
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    mapped = permission_scope(ctx.subject, "map.points.read")
    like = _escape_like(query)
    async with ctx.uow() as uow:
        incidents = (
            (
                await uow.session.execute(
                    _INCIDENTS,
                    {
                        "prefix": like.upper() + "%",
                        **_scope(summary),
                        "sg": sensitive.is_global,
                        "sr": list(sensitive.region_ids),
                        "ss": list(sensitive.station_ids),
                    },
                )
            ).all()
            if summary.any
            else []
        )
        units = (await uow.session.execute(_UNITS, {"contains": f"%{like}%"})).all()
        sensitive_args = {
            "sg": sensitive.is_global,
            "sr": list(sensitive.region_ids),
            "ss": list(sensitive.station_ids),
        }
        localities = (
            (
                await uow.session.execute(
                    _LOCALITIES, {"contains": f"%{like}%", **_scope(mapped), **sensitive_args}
                )
            ).all()
            if mapped.any and len(query) >= 3
            else []
        )
        # Sensitive categories are offered only to callers with that access everywhere they map.
        categories = (
            (
                await uow.session.execute(
                    _CATEGORIES, {"contains": f"%{like}%", "sensitive": sensitive.is_global}
                )
            ).all()
            if len(query) >= 3
            else []
        )
        period = parse_period(query)
        uow.audit(
            ctx.event(
                "SEARCH",
                object_type="search",
                filters={
                    "q": "[redacted: looks like a personal identifier]"
                    if looks_personal(query)
                    else query
                },
                after_meta={
                    "incidents": len(incidents),
                    "units": len(units),
                    "localities": len(localities),
                    "categories": len(categories),
                    "period": period is not None,
                },
            )
        )
        await uow.commit()
    return SearchOut(
        query=query,
        incidents=[
            IncidentHit(
                id=r.id,
                reference=r.source_record_reference,
                station_code=r.station_code,
                region_code=r.region_code,
            )
            for r in incidents
        ],
        units=[
            UnitHit(code=u.code, name=u.name, unit_type=u.unit_type, region_code=u.region_code)
            for u in units
        ],
        localities=[
            LocalityHit(locality=loc.locality, station_code=loc.station_code, records=loc.records)
            for loc in localities
        ],
        categories=[CategoryHit(code=c.code, label=c.name) for c in categories],
        period=period,
        not_available=["landmark", "road_segment"],
    )
