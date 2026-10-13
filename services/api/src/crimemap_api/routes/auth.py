"""The caller's own identity, and login events reported by the BFF after sign-in."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from crimemap_api.deps import AUTHENTICATED, Ctx
from crimemap_api.identity import store
from crimemap_api.policy.catalogue import PERMISSIONS
from crimemap_api.policy.engine import permission_scope
from crimemap_api.settings import Environment

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]


class RoleScopeOut(BaseModel):
    role: str
    regions: list[str]
    station_ids: list[UUID]


class MeOut(BaseModel):
    user_id: UUID
    username: str | None
    name: str | None
    mfa: bool
    environment: Environment
    classification: str
    roles: list[RoleScopeOut]
    # Permissions usable somewhere (MFA and validity considered). For hiding navigation only: every
    # endpoint still authorises each request itself.
    permissions: list[str]
    # Permissions the user's roles would grant if this session had been verified with MFA (so the
    # UI can say "sign in again with your authenticator" only when that would actually help).
    mfa_blocked_permissions: list[str]


@router.get("/me")
async def me(ctx: AuthenticatedCtx) -> MeOut:
    now = datetime.now(UTC)
    async with ctx.services.sessions() as session:
        codes = await store.region_codes_by_id(session)
    roles = [
        RoleScopeOut(
            role=g.role.value,
            regions=sorted(codes[r] for r in g.region_ids if r in codes),
            station_ids=sorted(g.station_ids),
        )
        for g in ctx.subject.grants
        if g.is_active(now)
    ]
    held = {p for p in PERMISSIONS if permission_scope(ctx.subject, p, now=now).any}
    with_mfa = replace(ctx.subject, mfa=True)
    return MeOut(
        user_id=ctx.user.id,
        username=ctx.user.username,
        name=ctx.user.name,
        mfa=ctx.claims.mfa,
        environment=ctx.services.settings.environment,
        classification="RESTRICTED",
        roles=sorted(roles, key=lambda r: r.role),
        permissions=sorted(held),
        mfa_blocked_permissions=sorted(
            p for p in PERMISSIONS if p not in held and permission_scope(with_mfa, p, now=now).any
        ),
    )


@router.post("/login-events", status_code=204)
async def login_event(ctx: AuthenticatedCtx) -> Response:
    """Called by the BFF once, right after the OIDC code exchange. Records LOGIN in the audit log."""
    async with ctx.uow() as uow:
        await store.record_login(uow.session, ctx.user.id, mfa=ctx.claims.mfa)
        uow.audit(
            ctx.event(
                "LOGIN",
                object_type="session",
                after_meta={"mfa": ctx.claims.mfa, "amr": list(ctx.claims.amr)},
            )
        )
        await uow.commit()
    return Response(status_code=204)
