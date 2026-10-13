"""Database engine and the per-request unit of work.

``UnitOfWork`` opens one transaction, sets the caller's scope as transaction-local settings (read by
row-level-security policies from Phase 2), buffers audit events, and writes them as the last
statements before commit. If anything fails, nothing — data or audit — is committed; the error path
audits the failure independently (see deps.py).
"""

from types import TracebackType
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from crimemap_api.audit.events import AuditContext, AuditEvent, insert_event
from crimemap_api.policy.catalogue import CASE_DATA_PERMISSIONS, MFA_EXEMPT_ROLES
from crimemap_api.policy.engine import Subject, permission_scope


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": "crimemap-api",
                "statement_timeout": "15000",
                "idle_in_transaction_session_timeout": "30000",
            }
        },
    )


class UnitOfWork:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        ctx: AuditContext,
        subject: Subject | None = None,
    ) -> None:
        self._sessions = sessions
        self._ctx = ctx
        self._subject = subject
        self._pending: list[AuditEvent] = []
        self._committed = False
        self.session: AsyncSession

    async def __aenter__(self) -> Self:
        self.session = self._sessions()
        await self.session.begin()
        if self._subject is not None:
            # Row-level security sees the territory of usable grants only: a grant that needs MFA
            # does not widen it for a session without MFA (the policy engine applies the same rule).
            usable = [
                g for g in self._subject.grants if self._subject.mfa or g.role in MFA_EXEMPT_ROLES
            ]
            regions = {r for g in usable for r in g.region_ids}
            stations = {s for g in usable for s in g.station_ids}
            scope_global = any(
                permission_scope(self._subject, p).is_global for p in CASE_DATA_PERMISSIONS
            )
            # AI insights only (auditors' global read); never widens case data (migration 0013).
            ai_global = permission_scope(self._subject, "ai.insight.read").is_global
            await self.session.execute(
                text(
                    "SELECT set_config('app.user_id', :uid, true),"
                    " set_config('app.region_ids', :regions, true),"
                    " set_config('app.station_ids', :stations, true),"
                    " set_config('app.scope_global', :scope_global, true),"
                    " set_config('app.ai_global', :ai_global, true),"
                    " set_config('app.request_id', :rid, true)"
                ),
                {
                    "uid": str(self._subject.user_id),
                    "regions": ",".join(sorted(map(str, regions))),
                    "stations": ",".join(sorted(map(str, stations))),
                    "scope_global": "on" if scope_global else "off",
                    "ai_global": "on" if ai_global else "off",
                    "rid": self._ctx.request_id or "",
                },
            )
        return self

    def audit(self, event: AuditEvent) -> None:
        """Queue an event; it is written in this transaction, immediately before commit."""
        self._pending.append(event)

    async def commit(self) -> None:
        for event in self._pending:
            await insert_event(self.session, event, self._ctx)
        self._pending.clear()
        await self.session.commit()
        self._committed = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.session.rollback()
        finally:
            await self.session.close()
