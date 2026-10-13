"""Failed logins at the identity provider reach the CRIMEMAP audit log exactly once."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from crimemap_worker.importer import import_idp_events
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .conftest import Database

pytestmark = pytest.mark.integration


@pytest.fixture
async def worker_engine(database: Database) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database.worker_async)
    yield engine
    await engine.dispose()


def failed_login(**overrides: Any) -> dict[str, Any]:
    return {
        "id": f"ev-{uuid.uuid4()}",
        "time": 1791729000000,
        "type": "LOGIN_ERROR",
        "clientId": "crimemap-web",
        "userId": None,
        "ipAddress": "198.51.100.4",
        "error": "invalid_user_credentials",
        "details": {"username": "someone"},
        **overrides,
    }


def rows_for(database: Database, idp_event_id: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.superuser) as conn:
        return conn.execute(
            "SELECT e.action, e.result, e.reason, host(e.ip_address), e.request_id "
            "FROM audit.audit_event e WHERE e.request_id = %s",
            [f"idp:{idp_event_id}"],
        ).fetchall()


async def test_failed_login_is_imported(worker_engine: AsyncEngine, database: Database) -> None:
    event = failed_login()
    imported = await import_idp_events(worker_engine, [event], environment="DEMO", source="test-kc")
    assert imported == 1
    assert rows_for(database, event["id"]) == [
        ("LOGIN_FAILED", "DENY", "invalid_user_credentials", "198.51.100.4", f"idp:{event['id']}")
    ]


async def test_reimport_is_idempotent(worker_engine: AsyncEngine, database: Database) -> None:
    event = failed_login()
    await import_idp_events(worker_engine, [event], environment="DEMO", source="test-kc")
    again = await import_idp_events(
        worker_engine, [event, event], environment="DEMO", source="test-kc"
    )
    assert again == 0
    assert len(rows_for(database, event["id"])) == 1


async def test_known_subject_is_linked_to_the_user(
    worker_engine: AsyncEngine, database: Database
) -> None:
    sub = str(uuid.uuid4())
    with psycopg.connect(database.migrator, autocommit=True) as conn:
        user_id = conn.execute(
            "INSERT INTO core.app_user (idp_subject) VALUES (%s) RETURNING id::text", [sub]
        ).fetchone()[0]  # type: ignore[index]
    event = failed_login(userId=sub)
    await import_idp_events(worker_engine, [event], environment="DEMO", source="test-kc")
    with psycopg.connect(database.superuser) as conn:
        actor = conn.execute(
            "SELECT actor_user_id::text FROM audit.audit_event WHERE request_id = %s",
            [f"idp:{event['id']}"],
        ).fetchone()
    assert actor == (user_id,)


async def test_cursor_advances_to_latest_event_time(
    worker_engine: AsyncEngine, database: Database
) -> None:
    source = f"kc-{uuid.uuid4()}"
    await import_idp_events(
        worker_engine,
        [failed_login(time=1000), failed_login(time=3000), failed_login(time=2000)],
        environment="DEMO",
        source=source,
    )
    with psycopg.connect(database.superuser) as conn:
        cursor = conn.execute(
            "SELECT last_event_ms FROM audit.idp_sync_cursor WHERE source = %s", [source]
        ).fetchone()
    assert cursor == (3000,)


async def test_irrelevant_events_are_skipped_but_marked_seen(worker_engine: AsyncEngine) -> None:
    event = failed_login(type="INTROSPECT_TOKEN")
    assert (
        await import_idp_events(worker_engine, [event], environment="DEMO", source="test-kc") == 0
    )


async def test_imported_events_keep_the_chain_valid(
    worker_engine: AsyncEngine, database: Database
) -> None:
    await import_idp_events(worker_engine, [failed_login()], environment="DEMO", source="test-kc")
    with psycopg.connect(database.app) as conn:
        assert conn.execute("SELECT ok FROM audit.verify_chain()").fetchone() == (True,)
