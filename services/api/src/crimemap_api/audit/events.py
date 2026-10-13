"""Writing audit events.

Two paths (ADR-008):
* ``insert_event`` — inside the request's own transaction, flushed just before commit, so a
  successful response always has its audit record (fail-closed) and the chain lock is held briefly.
* ``write_independent`` — its own short transaction, for denials and errors, so a rolled-back
  request can never erase the record of the attempt.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from ipaddress import ip_address as parse_ip
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

log = logging.getLogger(__name__)

Result = Literal["ALLOW", "DENY", "ERROR"]


@dataclass(frozen=True)
class AuditContext:
    """Request-level facts recorded with every event."""

    request_id: str | None
    environment: str
    ip_address: str | None = None
    user_agent: str | None = None

    @property
    def user_agent_hash(self) -> str | None:
        if not self.user_agent:
            return None
        return hashlib.sha256(self.user_agent.encode("utf-8", "replace")).hexdigest()[:32]


@dataclass(frozen=True)
class AuditEvent:
    action: str
    result: Result
    reason: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    actor_user_id: UUID | None = None
    actor_subject: str | None = None
    actor_roles: tuple[str, ...] = ()
    purpose: str | None = None
    filters: dict[str, Any] | None = None
    before_meta: dict[str, Any] | None = None
    after_meta: dict[str, Any] | None = None
    export_classification: str | None = None
    extra_context: dict[str, Any] = field(default_factory=dict)


_INSERT = text(
    """
    INSERT INTO audit.audit_event (
        actor_user_id, actor_subject, actor_roles, action, object_type, object_id, request_id,
        ip_address, user_agent_hash, purpose, filters, result, reason, before_meta, after_meta,
        export_classification, environment)
    VALUES (
        :actor_user_id, :actor_subject, :actor_roles, :action, :object_type, :object_id, :request_id,
        CAST(:ip_address AS inet), :user_agent_hash, :purpose, CAST(:filters AS jsonb), :result, :reason,
        CAST(:before_meta AS jsonb), CAST(:after_meta AS jsonb), :export_classification, :environment)
    """
)


def _json(value: dict[str, Any] | None) -> str | None:
    return None if value is None else json.dumps(value, default=str, sort_keys=True)


def _ip(value: str | None) -> str | None:
    """Only real IP addresses are stored (test clients report host names)."""
    if not value:
        return None
    try:
        return str(parse_ip(value))
    except ValueError:
        return None


def _params(event: AuditEvent, ctx: AuditContext) -> dict[str, Any]:
    return {
        "actor_user_id": event.actor_user_id,
        "actor_subject": event.actor_subject,
        "actor_roles": list(event.actor_roles),
        "action": event.action,
        "object_type": event.object_type,
        "object_id": event.object_id,
        "request_id": ctx.request_id,
        "ip_address": _ip(ctx.ip_address),
        "user_agent_hash": ctx.user_agent_hash,
        "purpose": event.purpose,
        "filters": _json(event.filters),
        "result": event.result,
        "reason": event.reason,
        "before_meta": _json(event.before_meta),
        "after_meta": _json(event.after_meta),
        "export_classification": event.export_classification,
        "environment": ctx.environment,
    }


async def insert_event(
    conn: AsyncSession | AsyncConnection, event: AuditEvent, ctx: AuditContext
) -> None:
    await conn.execute(_INSERT, _params(event, ctx))


async def write_independent(engine: AsyncEngine, event: AuditEvent, ctx: AuditContext) -> None:
    """Record an event in its own transaction. Failures are logged loudly, never swallowed silently."""
    try:
        async with engine.begin() as conn:
            await insert_event(conn, event, ctx)
    except Exception:
        log.exception(
            "audit_write_failed",
            extra={"action": event.action, "request_id": ctx.request_id},
        )
        raise
