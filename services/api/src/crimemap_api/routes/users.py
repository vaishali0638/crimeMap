"""User and role-grant administration (SYSTEM_ADMIN). Changes need a fresh login, are audited,
and can never be made by the affected user (separation of duties)."""

import base64
import binascii
import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from crimemap_api.deps import Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.identity import store
from crimemap_api.policy.catalogue import PERMISSIONS, ROLE_PERMISSIONS, Role

router = APIRouter(prefix="/api/v1", tags=["users"])
security_log = logging.getLogger("crimemap.security")

AdminCtx = Annotated[Ctx, Depends(Requirement("admin.users"))]


class UserOut(BaseModel):
    id: UUID
    username: str | None
    name: str | None
    email: str | None
    status: str
    mfa_status: str
    last_login_at: datetime | None
    is_demo: bool


class CursorPage(BaseModel):
    next_cursor: str | None
    has_more: bool


class UserPage(BaseModel):
    data: list[UserOut]
    pagination: CursorPage


class GrantOut(BaseModel):
    id: UUID
    user_id: UUID
    role: str
    region_codes: list[str]
    station_ids: list[UUID]
    valid_from: datetime
    valid_to: datetime | None
    grant_reason: str
    granted_by: UUID | None
    revoked_at: datetime | None


class GrantList(BaseModel):
    data: list[GrantOut]


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Role
    region_codes: list[Annotated[str, Field(pattern=r"^[A-Z]{2,5}$")]] = Field(max_length=4)
    valid_to: datetime | None = None
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("valid_to")
    @classmethod
    def _future(cls, v: datetime | None) -> datetime | None:
        if v is not None and (v.tzinfo is None or v <= datetime.now(UTC)):
            raise ValueError("valid_to must be a future, timezone-aware timestamp")
        return v


class RevocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=3, max_length=500)


class RoleOut(BaseModel):
    code: str
    permissions: list[str]


def _encode_cursor(user_id: UUID) -> str:
    return base64.urlsafe_b64encode(user_id.bytes).decode().rstrip("=")


def _decode_cursor(cursor: str) -> UUID:
    try:
        return UUID(bytes=base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (binascii.Error, ValueError):
        raise ProblemError(400, "invalid-cursor", "Invalid pagination cursor") from None


def _grant_out(g: store.GrantRow) -> GrantOut:
    return GrantOut(
        id=g.id,
        user_id=g.user_id,
        role=g.role,
        region_codes=list(g.region_codes),
        station_ids=list(g.station_ids),
        valid_from=g.valid_from,
        valid_to=g.valid_to,
        grant_reason=g.grant_reason,
        granted_by=g.granted_by,
        revoked_at=g.revoked_at,
    )


@router.get("/users")
async def list_users(
    ctx: AdminCtx,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
) -> UserPage:
    after = _decode_cursor(cursor) if cursor else None
    async with ctx.uow() as uow:
        rows = await store.list_users(uow.session, after=after, limit=limit + 1)
        page, has_more = rows[:limit], len(rows) > limit
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="user",
                filters={"limit": limit, "paged": cursor is not None},
                after_meta={"count": len(page)},
            )
        )
        await uow.commit()
    return UserPage(
        data=[
            UserOut(
                id=u.id,
                username=u.username,
                name=u.name,
                email=u.email,
                status=u.status,
                mfa_status=u.mfa_status,
                last_login_at=u.last_login_at,
                is_demo=u.is_demo,
            )
            for u in page
        ],
        pagination=CursorPage(
            next_cursor=_encode_cursor(page[-1].id) if has_more and page else None,
            has_more=has_more,
        ),
    )


@router.get("/users/{user_id}/role-grants")
async def list_role_grants(ctx: AdminCtx, user_id: UUID) -> GrantList:
    async with ctx.uow() as uow:
        if await store.get_user(uow.session, user_id) is None:
            raise ProblemError(404, "not-found", "Not found")
        grants = await store.list_grants(uow.session, user_id)
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="role_grant",
                object_id=str(user_id),
                after_meta={"count": len(grants)},
            )
        )
        await uow.commit()
    return GrantList(data=[_grant_out(g) for g in grants])


async def _check_change_allowed(ctx: Ctx, user_id: UUID, detail: dict[str, object]) -> None:
    ctx.require_fresh_auth("admin.users")
    if user_id == ctx.user.id:
        raise await ctx.deny(
            ctx.event(
                "PRIVILEGE_CHANGE",
                "DENY",
                reason="SEPARATION_OF_DUTIES",
                object_type="role_grant",
                object_id=str(user_id),
                after_meta=detail,
            ),
            409,
            "separation-of-duties",
            "You cannot change your own roles",
        )


@router.post("/users/{user_id}/role-grants", status_code=201)
async def create_role_grant(ctx: AdminCtx, user_id: UUID, body: GrantIn) -> GrantOut:
    detail: dict[str, object] = {
        "role": body.role.value,
        "region_codes": sorted(body.region_codes),
        "target_user_id": str(user_id),
    }
    await _check_change_allowed(ctx, user_id, detail)
    async with ctx.uow() as uow:
        if await store.get_user(uow.session, user_id) is None:
            raise ProblemError(404, "not-found", "Not found")
        regions = await store.region_ids_for_codes(uow.session, body.region_codes)
        unknown = sorted(set(body.region_codes) - set(regions))
        if unknown:
            raise ProblemError(
                422, "validation-error", "Unknown region code", detail=", ".join(unknown)
            )
        grant = await store.create_grant(
            uow.session,
            user_id=user_id,
            role=body.role,
            region_ids=list(regions.values()),
            valid_to=body.valid_to,
            reason=body.reason,
            granted_by=ctx.user.id,
        )
        uow.audit(
            ctx.event(
                "PRIVILEGE_CHANGE",
                object_type="role_grant",
                object_id=str(grant.id),
                reason=body.reason,
                after_meta=detail | {"valid_to": body.valid_to},
            )
        )
        await uow.commit()
    security_log.warning(
        "privilege_change", extra={"target_user_id": str(user_id), "role": body.role.value}
    )
    return _grant_out(grant)


@router.post("/users/{user_id}/role-grants/{grant_id}/revocations")
async def revoke_role_grant(
    ctx: AdminCtx, user_id: UUID, grant_id: UUID, body: RevocationIn
) -> GrantOut:
    await _check_change_allowed(ctx, user_id, {"revoked_grant_id": str(grant_id)})
    async with ctx.uow() as uow:
        grant = await store.get_grant(uow.session, user_id, grant_id)
        if grant is None:
            raise ProblemError(404, "not-found", "Not found")
        if grant.revoked_at is not None:
            raise ProblemError(409, "already-revoked", "Grant already revoked")
        await store.revoke_grant(uow.session, grant_id, revoked_by=ctx.user.id)
        uow.audit(
            ctx.event(
                "PRIVILEGE_CHANGE",
                object_type="role_grant",
                object_id=str(grant_id),
                reason=body.reason,
                before_meta={"role": grant.role, "region_codes": list(grant.region_codes)},
                after_meta={"revoked": True, "target_user_id": str(user_id)},
            )
        )
        updated = await store.get_grant(uow.session, user_id, grant_id)
        await uow.commit()
    security_log.warning(
        "privilege_change", extra={"target_user_id": str(user_id), "revoked": str(grant_id)}
    )
    if updated is None:
        raise ProblemError(404, "not-found", "Not found")
    return _grant_out(updated)


@router.get("/roles")
async def list_roles(ctx: AdminCtx) -> list[RoleOut]:
    return [RoleOut(code=r.value, permissions=sorted(ROLE_PERMISSIONS[r])) for r in Role]


@router.get("/permissions")
async def list_permissions(ctx: AdminCtx) -> list[str]:
    return sorted(PERMISSIONS)
