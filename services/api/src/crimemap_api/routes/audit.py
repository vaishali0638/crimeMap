"""Audit trail access (AUDITOR full; SYSTEM_ADMIN metadata only). Reading the trail is audited."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from crimemap_api.deps import Ctx, Requirement

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

AuditCtx = Annotated[Ctx, Depends(Requirement("audit.read"))]


class AuditEventOut(BaseModel):
    seq: int
    occurred_at: datetime
    action: str
    result: str
    reason: str | None
    actor_user_id: UUID | None
    actor_subject: str | None
    actor_roles: list[str]
    object_type: str | None
    object_id: str | None
    request_id: str | None
    purpose: str | None
    environment: str
    # Content fields: present for AUDITOR, omitted for metadata-only access.
    ip_address: str | None = None
    filters: dict[str, object] | None = None
    before_meta: dict[str, object] | None = None
    after_meta: dict[str, object] | None = None


class AuditPage(BaseModel):
    data: list[AuditEventOut]
    next_before_seq: int | None


class VerificationOut(BaseModel):
    ok: bool
    checked: int
    first_bad_seq: int | None
    problem: str | None


# Two fixed queries — no SQL is assembled at request time: metadata only, or with content.
_SELECT_METADATA = text(
    "SELECT seq, occurred_at, action, result, reason, actor_user_id, actor_subject, actor_roles, "
    "object_type, object_id, request_id, purpose, environment "
    "FROM audit.audit_event "
    "WHERE (CAST(:before AS bigint) IS NULL OR seq < CAST(:before AS bigint)) "
    "AND (CAST(:action AS text) IS NULL OR action = CAST(:action AS text)) "
    "ORDER BY seq DESC LIMIT :limit"
)
_SELECT_FULL = text(
    "SELECT seq, occurred_at, action, result, reason, actor_user_id, actor_subject, actor_roles, "
    "object_type, object_id, request_id, purpose, environment, "
    "host(ip_address) AS ip_address, filters, before_meta, after_meta "
    "FROM audit.audit_event "
    "WHERE (CAST(:before AS bigint) IS NULL OR seq < CAST(:before AS bigint)) "
    "AND (CAST(:action AS text) IS NULL OR action = CAST(:action AS text)) "
    "ORDER BY seq DESC LIMIT :limit"
)


@router.get("/events", response_model=AuditPage, response_model_exclude_unset=True)
async def list_events(
    ctx: AuditCtx,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    action: Annotated[str | None, Query(pattern=r"^[A-Z][A-Z_]{1,63}$")] = None,
) -> AuditPage:
    metadata_only = (
        ctx.decision is not None
        and ctx.decision.grant is not None
        and (ctx.decision.grant.note == "metadata")
    )
    query = _SELECT_METADATA if metadata_only else _SELECT_FULL
    async with ctx.uow() as uow:
        rows = (
            (
                await uow.session.execute(
                    query,
                    {"before": before_seq, "action": action, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        uow.audit(
            ctx.event(
                "AUDIT_VIEW",
                object_type="audit_event",
                filters={"limit": limit, "before_seq": before_seq, "action": action},
                after_meta={"count": len(rows), "metadata_only": metadata_only},
            )
        )
        await uow.commit()
    data = [AuditEventOut(**dict(r)) for r in rows]
    return AuditPage(data=data, next_before_seq=data[-1].seq if len(data) == limit else None)


@router.get("/verification")
async def verify(ctx: AuditCtx) -> VerificationOut:
    async with ctx.uow() as uow:
        row = (
            await uow.session.execute(
                text("SELECT ok, checked, first_bad_seq, problem FROM audit.verify_chain()")
            )
        ).one()
        uow.audit(
            ctx.event(
                "AUDIT_VERIFY",
                object_type="audit_chain",
                after_meta={"ok": row.ok, "checked": row.checked},
            )
        )
        await uow.commit()
    return VerificationOut(
        ok=row.ok, checked=row.checked, first_bad_seq=row.first_bad_seq, problem=row.problem
    )
