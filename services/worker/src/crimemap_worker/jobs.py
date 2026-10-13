"""Background jobs (Dramatiq actors) and the scheduler that enqueues them.

dramatiq crimemap_worker.jobs            # run workers
python -m crimemap_worker.jobs schedule  # run the scheduler loop
"""

import asyncio
import logging
import sys
import time
from datetime import UTC, date, datetime, timedelta

import dramatiq
import httpx
from dramatiq.brokers.redis import RedisBroker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.logging_config import configure_logging
from crimemap_worker.exports import PURGE_EXPORTS
from crimemap_worker.importer import import_idp_events, read_cursor
from crimemap_worker.ingest import check_source_health, ingest_inbox, reconcile_day
from crimemap_worker.keycloak_client import KeycloakEventsClient
from crimemap_worker.settings import WorkerSettings

log = logging.getLogger("crimemap.worker")
security_log = logging.getLogger("crimemap.security")

settings = WorkerSettings()
# RedisBroker.__init__ is unannotated upstream.
dramatiq.set_broker(RedisBroker(url=settings.redis_url.get_secret_value()))  # type: ignore[no-untyped-call]

# Retries use Dramatiq's exponential backoff: 10 s doubling up to 10 min, 5 attempts.
MAX_RETRIES, MIN_BACKOFF_MS, MAX_BACKOFF_MS = 5, 10_000, 600_000


async def _sync_idp_events() -> int:
    engine = create_async_engine(settings.database_url.get_secret_value())
    source = f"keycloak:{settings.keycloak_realm}"
    try:
        cursor = await read_cursor(engine, source)
        since = max(0, (cursor or int(time.time() * 1000) - 86_400_000) - settings.idp_overlap_ms)
        async with httpx.AsyncClient(follow_redirects=False) as http:
            client = KeycloakEventsClient(
                base_url=settings.keycloak_base_url,
                realm=settings.keycloak_realm,
                client_id=settings.keycloak_client_id,
                client_secret=settings.keycloak_client_secret.get_secret_value(),
                http=http,
            )
            events = await client.events_since(since)
        return await import_idp_events(
            engine, events, environment=settings.environment, source=source
        )
    finally:
        await engine.dispose()


@dramatiq.actor(
    queue_name="audit",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
)
def sync_idp_events() -> None:
    imported = asyncio.run(_sync_idp_events())
    log.info("idp_events_imported", extra={"count": imported})


async def _verify_audit_chain() -> bool:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT ok, checked, first_bad_seq, problem FROM audit.verify_chain()")
                )
            ).one()
    finally:
        await engine.dispose()
    if not row.ok:
        security_log.critical(
            "audit_chain_broken", extra={"first_bad_seq": row.first_bad_seq, "problem": row.problem}
        )
    else:
        log.info("audit_chain_verified", extra={"checked": row.checked})
    return bool(row.ok)


@dramatiq.actor(queue_name="audit", max_retries=0)
def verify_audit_chain() -> None:
    asyncio.run(_verify_audit_chain())


async def _ensure_partitions() -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as conn:
            today = datetime.now(UTC).date()
            for offset in range(3):
                year, month = divmod(today.month - 1 + offset, 12)
                await conn.execute(
                    text("SELECT audit.ensure_month_partition(:d)"),
                    {"d": date(today.year + year, month + 1, 1)},
                )
    finally:
        await engine.dispose()


@dramatiq.actor(
    queue_name="maintenance",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
)
def ensure_audit_partitions() -> None:
    asyncio.run(_ensure_partitions())


async def _ingest_inbox() -> dict[str, int]:
    if settings.inbox_dir is None:
        return {}
    key = (
        settings.demo_signing_key.get_secret_value().encode() if settings.demo_signing_key else b""
    )
    if len(key) < 32:
        raise RuntimeError(
            "inbox ingestion needs CRIMEMAP_DEMO_SIGNING_KEY (at least 32 characters)"
        )
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        return await ingest_inbox(engine, settings.inbox_dir, key, environment=settings.environment)
    finally:
        await engine.dispose()


@dramatiq.actor(
    queue_name="ingest",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
    time_limit=30 * 60 * 1000,
)
def ingest_inbox_batches() -> None:
    """Retried with exponential backoff on failure; files stay in the inbox until processed."""
    summary = asyncio.run(_ingest_inbox())
    log.info("inbox_ingested", extra={"batches": summary})


async def _check_source_health() -> dict[str, str]:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        return await check_source_health(engine, environment=settings.environment)
    finally:
        await engine.dispose()


@dramatiq.actor(
    queue_name="ingest",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
)
def check_sources() -> None:
    statuses = asyncio.run(_check_source_health())
    log.info("source_health_checked", extra={"statuses": statuses})


async def _reconcile(day: date) -> int:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as conn:
            rows = await reconcile_day(conn, day, environment=settings.environment)
    finally:
        await engine.dispose()
    return sum(int(r["unexplained"]) for r in rows)


@dramatiq.actor(
    queue_name="ingest",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
)
def reconcile_yesterday() -> None:
    """Daily reconciliation report for the previous UTC day (audited; imbalance opens DQ-REC-001)."""
    unexplained = asyncio.run(_reconcile(datetime.now(UTC).date() - timedelta(days=1)))
    (security_log.warning if unexplained else log.info)(
        "reconciliation_report", extra={"unexplained": unexplained}
    )


async def _purge_exports() -> int:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as conn:
            return int((await conn.execute(PURGE_EXPORTS)).scalar_one())
    finally:
        await engine.dispose()


@dramatiq.actor(
    queue_name="maintenance",
    max_retries=MAX_RETRIES,
    min_backoff=MIN_BACKOFF_MS,
    max_backoff=MAX_BACKOFF_MS,
)
def purge_expired_exports() -> None:
    """Delete generated exports 24 hours after creation (the stated download-expiry policy)."""
    log.info("expired_exports_purged", extra={"deleted": asyncio.run(_purge_exports())})


def schedule() -> None:
    """Enqueue periodic jobs. One scheduler process per deployment."""
    configure_logging()
    last: dict[str, float] = {}
    intervals = {
        "sync_idp_events": settings.idp_poll_seconds,
        "verify_audit_chain": 24 * 3600,
        "ensure_audit_partitions": 24 * 3600,
        "ingest_inbox_batches": settings.inbox_poll_seconds,
        "check_sources": settings.source_health_seconds,
        "reconcile_yesterday": 24 * 3600,
        "purge_expired_exports": 3600,
    }
    actors = {
        "sync_idp_events": sync_idp_events,
        "verify_audit_chain": verify_audit_chain,
        "ensure_audit_partitions": ensure_audit_partitions,
        "ingest_inbox_batches": ingest_inbox_batches,
        "check_sources": check_sources,
        "reconcile_yesterday": reconcile_yesterday,
        "purge_expired_exports": purge_expired_exports,
    }
    while True:
        now = time.monotonic()
        for name, every in intervals.items():
            if now - last.get(name, -1e12) >= every:
                actors[name].send()
                last[name] = now
        time.sleep(5)


if __name__ == "__main__" and sys.argv[1:] == ["schedule"]:
    schedule()
