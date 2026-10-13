"""Import identity-provider events into the audit log (exactly once per event).

Runs as the crimemap_worker role. Each event is claimed in ``audit.idp_event_import`` (primary key on
the event key) before its audit row is written, in one transaction, so re-imports and overlapping
polling windows never create duplicates.
"""

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from crimemap_api.audit.events import AuditContext, insert_event
from crimemap_worker.keycloak_events import event_key, map_event


async def import_idp_events(
    engine: AsyncEngine, events: Iterable[dict[str, Any]], *, environment: str, source: str
) -> int:
    """Write new security-relevant events; returns how many audit rows were created."""
    imported = 0
    latest_ms: int | None = None
    async with engine.begin() as conn:
        for raw in events:
            if isinstance(raw.get("time"), int):
                latest_ms = max(latest_ms or raw["time"], raw["time"])
            key = event_key(raw)
            claimed = (
                await conn.execute(
                    text(
                        "INSERT INTO audit.idp_event_import (event_key) VALUES (:k) "
                        "ON CONFLICT (event_key) DO NOTHING RETURNING event_key"
                    ),
                    {"k": key},
                )
            ).first()
            if claimed is None:
                continue
            mapped = map_event(raw)
            if mapped is None:
                continue
            event, ip = mapped
            if event.actor_subject:
                user_id = (
                    await conn.execute(
                        text("SELECT id FROM core.app_user WHERE idp_subject = :s"),
                        {"s": event.actor_subject},
                    )
                ).scalar_one_or_none()
                if user_id is not None:
                    event = replace(event, actor_user_id=user_id)
            ctx = AuditContext(
                request_id=f"idp:{raw.get('id') or key}", environment=environment, ip_address=ip
            )
            await insert_event(conn, event, ctx)
            imported += 1
        if latest_ms is not None:
            await conn.execute(
                text(
                    "INSERT INTO audit.idp_sync_cursor (source, last_event_ms) VALUES (:s, :t) "
                    "ON CONFLICT (source) DO UPDATE SET last_event_ms = "
                    "GREATEST(audit.idp_sync_cursor.last_event_ms, EXCLUDED.last_event_ms), updated_at = now()"
                ),
                {"s": source, "t": latest_ms},
            )
    return imported


async def read_cursor(engine: AsyncEngine, source: str) -> int | None:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT last_event_ms FROM audit.idp_sync_cursor WHERE source = :s"),
                {"s": source},
            )
        ).scalar_one_or_none()
