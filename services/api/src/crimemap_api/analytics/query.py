"""Analytics Workbench filters (the unified filter bar) and their resolution against the caller's
scope. One model serves the GET endpoints, evidence snapshots and exports, so a snapshot or export
always re-runs exactly the analysis the user saw.

Rules (ADR-035):
- Territory: region, stations and beats must lie inside the scope of the permission in use, else an
  audited 403 (never silently narrowed).
- Record types are never mixed by accident: the default is CASE; ``COMBINED`` must be chosen
  explicitly and every result then carries a caveat.
- Case-status filters need ``incident.summary.read`` for the region ("if authorised").
- Callers without sensitive-category access covering the selection get sensitive records only as
  a combined group of at least 3 per bucket (ADR-012) and cannot filter on a sensitive category.
- A DATA_STEWARD's ``analytics.read`` covers the Data Quality section only (role matrix).
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.deps import Ctx
from crimemap_api.http import ProblemError
from crimemap_api.policy.catalogue import Role
from crimemap_api.policy.engine import PermissionScope, Subject, Target, authorize, permission_scope
from crimemap_api.routes.map import _period, _scope_args
from crimemap_api.territory import RegionUnits, load_region, refuse, resolve

Section = Literal["overview", "location", "case_trends", "comparison", "data_quality"]
RecordTypeFilter = Literal["CASE", "ERSS_CALL", "ACCIDENT", "TRAFFIC_EVENT", "COMBINED"]
Comparison = Literal["PREVIOUS", "YEAR", "NONE"]
Grain = Literal["day", "week", "month"]
CompareKind = Literal["REGION", "STATION", "BEAT", "PERIOD", "YEAR", "CATEGORY"]

CODE = r"^[A-Z0-9_-]{2,40}$"


class AnalyticsQuery(BaseModel):
    """The unified filter bar. Every field is validated; unknown fields are refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    region: str = Field(pattern=r"^[A-Z]{2,5}$")
    station: list[str] = Field(default_factory=list, max_length=20)
    beat: list[str] = Field(default_factory=list, max_length=50)
    category: list[str] = Field(default_factory=list, max_length=20)
    locality: str | None = Field(default=None, min_length=2, max_length=100)
    record_type: RecordTypeFilter = "CASE"
    status: list[str] = Field(default_factory=list, max_length=20)
    min_confidence: Literal["0", "0.4", "0.8"] = "0"
    period_from: date | None = None
    period_to: date | None = None
    time_basis: Literal["OCCURRENCE", "REPORTED"] = "OCCURRENCE"
    hour_from: int | None = Field(default=None, ge=0, le=23)
    hour_to: int | None = Field(default=None, ge=0, le=23)
    comparison: Comparison = "PREVIOUS"
    grain: Grain | None = None
    hex_size_m: Literal["250", "500", "1000", "2000"] = "500"
    unit: Literal["HEX", "BEAT", "STATION"] = "HEX"
    # Comparison section only
    compare: CompareKind | None = None
    a: str | None = Field(default=None, pattern=CODE)
    b: str | None = Field(default=None, pattern=CODE)

    @model_validator(mode="after")
    def _checks(self) -> "AnalyticsQuery":
        if (self.hour_from is None) != (self.hour_to is None):
            raise ValueError("give both hour_from and hour_to, or neither")
        for code in [*self.station, *self.beat, *self.category, *self.status]:
            if not re.match(CODE, code):
                raise ValueError("invalid code")
        return self

    def as_params(self) -> dict[str, Any]:
        """Canonical, JSON-safe form (stored with snapshots and exports)."""
        return self.model_dump(mode="json", exclude_none=True)


@dataclass
class Window:
    start: date
    end: date
    p_from: datetime
    p_to: datetime


@dataclass
class Resolved:
    """A query resolved against the caller's scope, ready for SQL."""

    query: AnalyticsQuery
    units: RegionUnits
    scope: PermissionScope
    sensitive: PermissionScope
    station_ids: list[UUID] | None
    beat_ids: list[UUID] | None
    category_ids: list[UUID] | None
    category_label: str
    record_types: list[str] | None
    may_mask: bool
    current: Window
    comparison: Window | None
    may_see_status: bool
    caveats: list[str]

    def args(
        self, window: Window, *, basis: str | None = None, categories: list[UUID] | None = None
    ) -> dict[str, Any]:
        q = self.query
        return {
            "region": self.units.region.id,
            "stations": self.station_ids,
            "beats": self.beat_ids,
            "categories": categories if categories is not None else self.category_ids,
            "record_types": self.record_types,
            "statuses": q.status or None,
            "p_from": window.p_from,
            "p_to": window.p_to,
            "basis": basis or q.time_basis,
            "min_conf": float(q.min_confidence),
            **_scope_args("s", self.scope),
            "locality": f"%{_escape_like(q.locality)}%" if q.locality else None,
            "hour_from": q.hour_from,
            "hour_to": q.hour_to,
            **_scope_args("sens_", self.sensitive),
        }

    def coverage(self) -> tuple[list[UUID], list[UUID] | None]:
        """Every unit this result covers: the region, and the stations when not region-wide. A
        station-scoped caller's region-wide view covers their own stations only."""
        if self.station_ids:
            return [self.units.region.id], list(self.station_ids)
        if self.scope.is_global or self.units.region.id in self.scope.region_ids:
            return [self.units.region.id], None
        own = [s.id for s in self.units.stations.values() if s.id in self.scope.station_ids]
        return [self.units.region.id], own

    def filters_applied(self) -> dict[str, Any]:
        q = self.query
        out: dict[str, Any] = {
            "region": q.region,
            "period": f"{self.current.start.isoformat()}/{self.current.end.isoformat()}",
            "time_basis": q.time_basis.lower(),
            "record_type": q.record_type,
        }
        for name in ("station", "beat", "category", "status"):
            if getattr(q, name):
                out[name] = getattr(q, name)
        if q.locality:
            out["locality"] = q.locality
        if q.hour_from is not None:
            out["time_of_day"] = f"{q.hour_from:02d}:00-{q.hour_to:02d}:59 IST"
        if q.min_confidence != "0":
            out["min_location_confidence"] = q.min_confidence
        if self.comparison:
            out["comparison_period"] = (
                f"{self.comparison.start.isoformat()}/{self.comparison.end.isoformat()}"
            )
        return out


def _escape_like(value: str | None) -> str:
    return (value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _window(start: date, end: date) -> Window:
    from crimemap_api.routes.map import IST

    return Window(
        start,
        end,
        datetime.combine(start, datetime.min.time(), IST),
        datetime.combine(end + timedelta(days=1), datetime.min.time(), IST),
    )


def _year_before(d: date) -> date:
    """The same calendar day a year earlier; 29 February becomes 28 February (each end separately)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def comparison_window(current: Window, kind: Comparison) -> Window | None:
    if kind == "NONE":
        return None
    if kind == "YEAR":
        return _window(_year_before(current.start), _year_before(current.end))
    length = (current.end - current.start).days + 1
    return _window(current.start - timedelta(days=length), current.start - timedelta(days=1))


def section_scope(
    ctx: Ctx, section: Section, permission: str = "analytics.read"
) -> PermissionScope:
    """The territory in which the caller may use ``permission`` for this section."""
    if section == "data_quality":
        return permission_scope(ctx.subject, permission)
    # A steward's analytics grant is for the Data Quality section only.
    others = tuple(g for g in ctx.subject.grants if g.role is not Role.DATA_STEWARD)
    subject = Subject(
        user_id=ctx.subject.user_id,
        grants=others,
        mfa=ctx.subject.mfa,
        auth_time=ctx.subject.auth_time,
    )
    return permission_scope(subject, permission)


_CATEGORIES = text(
    "SELECT id, code, name, is_sensitive FROM core.analytical_category WHERE code = ANY (:c)"
)
_STATUSES = text("SELECT DISTINCT code FROM core.status_definition WHERE code = ANY (:c)")


async def resolve_query(
    ctx: Ctx,
    session: AsyncSession,
    q: AnalyticsQuery,
    section: Section,
    permission: str = "analytics.read",
) -> Resolved:
    scope = section_scope(ctx, section, permission)
    if not scope.any:
        raise refuse(ctx, permission)
    units = await load_region(session, q.region)
    selection = resolve(ctx, permission, scope, units, q.station, q.beat)
    units = selection.units
    start, end, p_from, p_to = _period(q.period_from, q.period_to)
    current = Window(start, end, p_from, p_to)
    sensitive = permission_scope(ctx.subject, "incident.sensitive_category.read")
    # Decided from region-wide grants only (never from the data): station-based sensitive access still
    # combines records outside those stations, including records with no station.
    may_mask = not (sensitive.is_global or units.region.id in sensitive.region_ids)
    category_ids: list[UUID] | None = None
    category_label = "All categories"
    if q.category:
        found = {r.code: r for r in await session.execute(_CATEGORIES, {"c": q.category})}
        if set(q.category) - set(found):
            raise ProblemError(422, "validation-error", "Unknown category")
        if may_mask and any(found[c].is_sensitive for c in q.category):
            raise refuse(ctx, "incident.sensitive_category.read")
        category_ids = [found[c].id for c in q.category]
        category_label = ", ".join(found[c].name for c in q.category)
    target = Target(region_id=units.region.id)
    may_see_status = authorize(ctx.subject, "incident.summary.read", target=target).allowed
    if q.status:
        if not may_see_status:
            raise refuse(ctx, "incident.summary.read")
        known = {r[0] for r in await session.execute(_STATUSES, {"c": q.status})}
        if set(q.status) - known:
            raise ProblemError(422, "validation-error", "Unknown status")
    caveats = ["Synthetic demo data and synthetic boundaries; not official jurisdictions."]
    if q.record_type == "COMBINED":
        caveats.append(
            "Calls, accidents, traffic events and cases are combined because you chose 'all record types'; "
            "they measure different things and should not be read as one statistic."
        )
    if may_mask:
        caveats.append(
            "Sensitive categories are shown only as one combined total, and only when it is at least 3; "
            "they are not included in charts, maps or tables by time or place."
        )
    if q.time_basis == "OCCURRENCE":
        caveats.append(
            "Records without an occurrence time are not included; choose report time to include them."
        )
    return Resolved(
        query=q,
        units=units,
        scope=scope,
        sensitive=sensitive,
        station_ids=selection.station_ids,
        beat_ids=selection.beat_ids,
        category_ids=category_ids,
        category_label=category_label,
        record_types=None if q.record_type == "COMBINED" else [q.record_type],
        may_mask=may_mask,
        current=current,
        comparison=comparison_window(current, q.comparison),
        may_see_status=may_see_status,
        caveats=caveats,
    )
