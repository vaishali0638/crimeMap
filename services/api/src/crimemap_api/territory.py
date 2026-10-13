"""Territory resolution shared by the map, search and operational-record endpoints.

A request names territory by code (region, stations, beats). Every named unit must lie inside the
territory of the permission being used; anything outside is refused (403, audited by the
AuthzDenied handler), never silently narrowed (data-classification §2). A station-scoped caller who
names only a region gets their own stations in it (that is their whole territory, not a narrowing
of what they asked for).
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.deps import AuthzDenied, Ctx
from crimemap_api.policy.engine import DenyReason, PermissionScope


@dataclass(frozen=True)
class Unit:
    id: UUID
    code: str
    name: str
    unit_type: str
    parent_id: UUID | None


@dataclass
class RegionUnits:
    region: Unit
    crs: int
    stations: dict[str, Unit] = field(default_factory=dict)
    beats: dict[str, Unit] = field(default_factory=dict)

    def station_of(self, beat: Unit) -> Unit | None:
        return next((s for s in self.stations.values() if s.id == beat.parent_id), None)


_REGION_UNITS = text(
    """
    SELECT o.id, o.code, o.name, o.unit_type, o.parent_id, r.analysis_crs
    FROM core.organizational_unit r
    JOIN core.organizational_unit o
      ON o.id = r.id OR o.parent_id = r.id
      OR o.parent_id IN (SELECT s.id FROM core.organizational_unit s WHERE s.parent_id = r.id)
    WHERE r.unit_type = 'REGION' AND r.code = :code
    ORDER BY o.code
    """
)


async def load_region(session: AsyncSession, code: str) -> RegionUnits | None:
    rows = (await session.execute(_REGION_UNITS, {"code": code})).all()
    if not rows:
        return None
    units = [Unit(r.id, r.code, r.name, r.unit_type, r.parent_id) for r in rows]
    region = next(u for u in units if u.unit_type == "REGION")
    result = RegionUnits(region=region, crs=int(rows[0].analysis_crs))
    for u in units:
        if u.unit_type == "STATION":
            result.stations[u.code] = u
        elif u.unit_type == "BEAT":
            result.beats[u.code] = u
    return result


def refuse(ctx: Ctx, permission: str) -> AuthzDenied:
    return AuthzDenied(ctx, permission, DenyReason.OUT_OF_SCOPE)


def region_allowed(scope: PermissionScope, units: RegionUnits) -> bool:
    return (
        scope.is_global
        or units.region.id in scope.region_ids
        or any(s.id in scope.station_ids for s in units.stations.values())
    )


def station_allowed(scope: PermissionScope, units: RegionUnits, station: Unit) -> bool:
    return scope.is_global or units.region.id in scope.region_ids or station.id in scope.station_ids


@dataclass(frozen=True)
class Selection:
    units: RegionUnits
    station_ids: list[UUID] | None  # None = whole region (within scope)
    beat_ids: list[UUID] | None


def resolve(
    ctx: Ctx,
    permission: str,
    scope: PermissionScope,
    units: RegionUnits | None,
    stations: list[str],
    beats: list[str],
) -> Selection:
    """Validate requested units against ``scope``; raise (audited 403) on anything outside it."""
    if units is None or not region_allowed(scope, units):
        raise refuse(ctx, permission)
    station_ids: list[UUID] = []
    for code in stations:
        st = units.stations.get(code)
        if st is None or not station_allowed(scope, units, st):
            raise refuse(ctx, permission)
        station_ids.append(st.id)
    beat_ids: list[UUID] = []
    for code in beats:
        beat = units.beats.get(code)
        parent = units.station_of(beat) if beat else None
        if beat is None or parent is None or not station_allowed(scope, units, parent):
            raise refuse(ctx, permission)
        beat_ids.append(beat.id)
    return Selection(units, station_ids or None, beat_ids or None)
