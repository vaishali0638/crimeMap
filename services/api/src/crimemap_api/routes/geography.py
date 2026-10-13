"""Synthetic geography: regions, stations, beats, current boundaries, and dual-control approval of
new boundary versions (ADR-013, ADR-026).

Unit lists and outlines are reference data and are available to any authenticated user. Version
history needs ``lineage.read``. Approving a version needs ``admin.config`` (SYSTEM_ADMIN slot) or
``dq.manage`` in the boundary's region (DATA_STEWARD slot), a fresh login, and a second, different
person for the other slot.
"""

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from crimemap_api.deps import AUTHENTICATED, Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.engine import Target

router = APIRouter(prefix="/api/v1", tags=["geography"])

AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]
LineageCtx = Annotated[Ctx, Depends(Requirement("lineage.read"))]

DATA_LABEL = "DEMO / SYNTHETIC DATA"
UnitCode = Annotated[str, Query(pattern=r"^[A-Z0-9-]{2,20}$")]


class RegionOut(BaseModel):
    id: UUID
    code: str
    name: str
    is_demo: bool
    station_count: int


class StationOut(BaseModel):
    id: UUID
    code: str
    name: str
    region_code: str
    is_demo: bool


class BeatOut(BaseModel):
    id: UUID
    code: str
    name: str
    station_code: str
    is_demo: bool


class ListOut[T](BaseModel):
    data: list[T]


class BoundaryVersionOut(BaseModel):
    id: UUID
    version: int
    approval_status: str
    source: str
    valid_from: datetime
    valid_to: datetime | None
    admin_approved: bool
    steward_approved: bool
    created_at: datetime


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_role: Literal["SYSTEM_ADMIN", "DATA_STEWARD"]


class ApprovalOut(BaseModel):
    boundary_id: UUID
    unit_code: str
    version: int
    approval_status: str


@router.get("/regions")
async def list_regions(ctx: AuthenticatedCtx) -> ListOut[RegionOut]:
    async with ctx.services.sessions() as session:
        rows = await session.execute(
            text(
                "SELECT r.id, r.code, r.name, r.is_demo, "
                "(SELECT count(*) FROM core.organizational_unit s "
                " WHERE s.parent_id = r.id AND s.unit_type = 'STATION') AS station_count "
                "FROM core.organizational_unit r WHERE r.unit_type = 'REGION' ORDER BY r.code"
            )
        )
        return ListOut(data=[RegionOut(**r._mapping) for r in rows])


@router.get("/stations")
async def list_stations(
    ctx: AuthenticatedCtx, region: UnitCode | None = None
) -> ListOut[StationOut]:
    async with ctx.services.sessions() as session:
        rows = await session.execute(
            text(
                "SELECT s.id, s.code, s.name, r.code AS region_code, s.is_demo "
                "FROM core.organizational_unit s JOIN core.organizational_unit r ON r.id = s.parent_id "
                "WHERE s.unit_type = 'STATION' AND (CAST(:region AS text) IS NULL OR r.code = :region) "
                "ORDER BY s.code"
            ),
            {"region": region},
        )
        return ListOut(data=[StationOut(**r._mapping) for r in rows])


@router.get("/beats")
async def list_beats(ctx: AuthenticatedCtx, station: UnitCode | None = None) -> ListOut[BeatOut]:
    async with ctx.services.sessions() as session:
        rows = await session.execute(
            text(
                "SELECT b.id, b.code, b.name, s.code AS station_code, b.is_demo "
                "FROM core.organizational_unit b JOIN core.organizational_unit s ON s.id = b.parent_id "
                "WHERE b.unit_type = 'BEAT' AND (CAST(:station AS text) IS NULL OR s.code = :station) "
                "ORDER BY b.code"
            ),
            {"station": station},
        )
        return ListOut(data=[BeatOut(**r._mapping) for r in rows])


@router.get("/boundaries")
async def current_boundaries(
    ctx: AuthenticatedCtx,
    level: Literal["REGION", "STATION", "BEAT"] = "REGION",
    region: UnitCode | None = None,
) -> dict[str, Any]:
    """Boundaries currently in force, as a GeoJSON FeatureCollection (coordinates to ~1 m)."""
    async with ctx.services.sessions() as session:
        rows = await session.execute(
            text(
                """
                SELECT b.code, b.name, b.unit_type, b.version, b.approval_status, b.source, b.is_demo,
                       ST_AsGeoJSON(b.geometry, 5) AS geometry
                FROM core.current_boundary b
                LEFT JOIN core.organizational_unit p1 ON p1.id = b.parent_id
                LEFT JOIN core.organizational_unit p2 ON p2.id = p1.parent_id
                WHERE b.unit_type = :level
                  AND (CAST(:region AS text) IS NULL
                       OR (b.unit_type = 'REGION' AND b.code = :region)
                       OR (b.unit_type = 'STATION' AND p1.code = :region)
                       OR (b.unit_type = 'BEAT' AND p2.code = :region))
                ORDER BY b.code
                """
            ),
            {"level": level, "region": region},
        )
        features = [
            {
                "type": "Feature",
                "geometry": json.loads(r.geometry),
                "properties": {
                    "code": r.code,
                    "name": r.name,
                    "unit_type": r.unit_type,
                    "version": r.version,
                    "approval_status": r.approval_status,
                    "source": r.source,
                    "is_demo": r.is_demo,
                },
            }
            for r in rows
        ]
    return {"type": "FeatureCollection", "data_label": DATA_LABEL, "features": features}


_UNIT_REGION = text(
    """
    SELECT o.code, CASE o.unit_type WHEN 'REGION' THEN o.id WHEN 'STATION' THEN p1.id
                                    ELSE p2.id END AS region_id
    FROM core.organizational_unit o
    LEFT JOIN core.organizational_unit p1 ON p1.id = o.parent_id
    LEFT JOIN core.organizational_unit p2 ON p2.id = p1.parent_id
    WHERE o.code = :code AND o.unit_type IN ('REGION', 'STATION', 'BEAT')
    """
)

_BOUNDARY_UNIT = text(
    """
    SELECT o.code, b.version, b.approval_status,
           CASE o.unit_type WHEN 'REGION' THEN o.id WHEN 'STATION' THEN p1.id ELSE p2.id END AS region_id
    FROM core.geographic_boundary b
    JOIN core.organizational_unit o ON o.id = b.org_unit_id
    LEFT JOIN core.organizational_unit p1 ON p1.id = o.parent_id
    LEFT JOIN core.organizational_unit p2 ON p2.id = p1.parent_id
    WHERE b.id = :id
    """
)


@router.get("/boundaries/{unit_code}/versions")
async def boundary_versions(
    ctx: LineageCtx, unit_code: Annotated[str, Path(pattern=r"^[A-Z0-9-]{2,20}$")]
) -> ListOut[BoundaryVersionOut]:
    """Version history, authorised against the unit's region (``lineage.read`` is region-scoped
    for stewards and global for auditors and administrators)."""
    async with ctx.services.sessions() as session:
        unit = (await session.execute(_UNIT_REGION, {"code": unit_code})).one_or_none()
        if unit is None:
            raise ProblemError(404, "not-found", "Unknown unit")
        ctx.require("lineage.read", target=Target(region_id=unit.region_id))
        rows = (
            await session.execute(
                text(
                    "SELECT b.id, b.version, b.approval_status, b.source, b.valid_from, b.valid_to, "
                    "b.approved_admin_by IS NOT NULL AS admin_approved, "
                    "b.approved_steward_by IS NOT NULL AS steward_approved, b.created_at "
                    "FROM core.geographic_boundary b JOIN core.organizational_unit o ON o.id = b.org_unit_id "
                    "WHERE o.code = :code ORDER BY b.version DESC"
                ),
                {"code": unit_code},
            )
        ).all()
    return ListOut(data=[BoundaryVersionOut(**r._mapping) for r in rows])


# SQLSTATEs raised by core.approve_boundary (migration 0004). CM00x are CRIMEMAP's own codes, so a
# genuine privilege error (42501) is never mistaken for a separation-of-duties refusal.
_DB_ERRORS = {
    "55000": (409, "invalid-state-transition", "This boundary is not awaiting this approval"),
    "CM001": (409, "separation-of-duties", "The second approval must come from a different person"),
    "CM002": (403, "forbidden", "Forbidden"),
    "P0002": (404, "not-found", "Not found"),
}


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


@router.post("/boundaries/{boundary_id}/approvals")
async def approve_boundary(
    ctx: AuthenticatedCtx, boundary_id: UUID, body: ApprovalIn
) -> ApprovalOut:
    """Dual-control approval (ADR-026). The caller must hold the slot's permission and a fresh login
    *before* anything about the boundary is revealed; the database function then re-checks the
    approver (taken from the session, never from the request) and separation of duties."""
    if body.as_role == "SYSTEM_ADMIN":
        permission, slot = "admin.config", "ADMIN"
    else:
        permission, slot = "dq.manage", "STEWARD"
    ctx.decision = ctx.require(permission)
    ctx.require_fresh_auth(permission)

    async with ctx.services.sessions() as session:
        unit = (await session.execute(_BOUNDARY_UNIT, {"id": boundary_id})).one_or_none()
    if unit is None:
        raise await ctx.deny(
            ctx.event(
                "BOUNDARY_APPROVAL",
                "DENY",
                reason="not-found",
                object_type="geographic_boundary",
                object_id=str(boundary_id),
            ),
            404,
            "not-found",
            "Not found",
        )
    if slot == "STEWARD":
        ctx.decision = ctx.require(permission, target=Target(region_id=unit.region_id))

    event_base: dict[str, Any] = {
        "object_type": "geographic_boundary",
        "object_id": str(boundary_id),
        "before_meta": {"approval_status": unit.approval_status, "unit": unit.code},
    }
    async with ctx.uow() as uow:
        try:
            status = (
                await uow.session.execute(
                    text("SELECT core.approve_boundary(:id, :slot)"),
                    {"id": boundary_id, "slot": slot},
                )
            ).scalar_one()
        except DBAPIError as exc:
            code = _sqlstate(exc)
            if code not in _DB_ERRORS:
                raise
            status_code, slug, title = _DB_ERRORS[code]
            await uow.session.rollback()
            raise await ctx.deny(
                ctx.event("BOUNDARY_APPROVAL", "DENY", reason=slug, **event_base),
                status_code,
                slug,
                title,
            ) from None
        uow.audit(
            ctx.event(
                "BOUNDARY_APPROVAL",
                **event_base,
                after_meta={"approval_status": status, "slot": slot, "version": unit.version},
            )
        )
        await uow.commit()
    return ApprovalOut(
        boundary_id=boundary_id, unit_code=unit.code, version=unit.version, approval_status=status
    )
