"""Audit-derived alert signals respond to the events they watch (Phase 8, ADR-038)."""

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.audit.events import AuditContext, AuditEvent, write_independent
from crimemap_api.metrics import SIGNALS, read_signals
from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration


async def signals(database: Database) -> dict[str, float]:
    engine = create_async_engine(database.app_async)
    try:
        return await read_signals(engine)
    finally:
        await engine.dispose()


async def test_every_signal_reads_as_the_application_role(database: Database) -> None:
    values = await signals(database)
    assert set(values) == {name for name, _, _ in SIGNALS}
    assert all(v >= 0 for v in values.values())


async def test_denials_privilege_changes_and_new_regions_raise_their_signals(
    api: httpx.AsyncClient, make_user, ingested: Ingested, database: Database
) -> None:  # type: ignore[no-untyped-def]
    before = await signals(database)
    # Repeated denied access: one user refused 12 times.
    sub, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    for _ in range(12):
        assert (await api.get("/api/v1/users", headers=bearer(mint(sub)))).status_code == 403
    # A privilege change.
    admin, _ = make_user(Role.SYSTEM_ADMIN)
    _, target = make_user()
    granted = await api.post(
        f"/api/v1/users/{target}/role-grants",
        json={"role": "DATA_STEWARD", "region_codes": ["MAH"], "reason": "signal test"},
        headers=bearer(mint(admin)),
    )
    assert granted.status_code == 201
    # A user opening a region for the first time.
    analyst, _ = make_user(Role.CRIME_ANALYST, regions=("YAN",))
    shown = await api.get(
        "/api/v1/map/features",
        params={
            "region": "YAN",
            "aggregation": "BEAT",
            "period_from": "2026-01-01",
            "period_to": "2026-03-31",
        },
        headers=bearer(mint(analyst)),
    )
    assert shown.status_code == 200, shown.text
    after = await signals(database)
    assert after["crimemap_security_denied_15m"] >= before["crimemap_security_denied_15m"] + 12
    assert after["crimemap_security_denied_max_per_source_15m"] >= 12
    assert (
        after["crimemap_security_privilege_changes_1h"]
        >= before["crimemap_security_privilege_changes_1h"] + 1
    )
    assert (
        after["crimemap_security_new_region_actors_1h"]
        >= before["crimemap_security_new_region_actors_1h"] + 1
    )


async def test_ingestion_and_ai_failures_raise_their_signals(database: Database) -> None:
    before = await signals(database)
    engine = create_async_engine(database.app_async)
    ctx = AuditContext(request_id=f"signal-test:{uuid.uuid4()}", environment="DEMO")
    try:
        await write_independent(
            engine, AuditEvent(action="INGEST_FAILED", result="ERROR", reason="test"), ctx
        )
        await write_independent(
            engine, AuditEvent(action="INGEST_REJECTED", result="DENY", reason="test"), ctx
        )
        await write_independent(
            engine, AuditEvent(action="AI_RUN", result="ERROR", reason="test"), ctx
        )
        for _ in range(5):
            await write_independent(
                engine,
                AuditEvent(action="EXPORT_REQUEST", result="ALLOW", actor_user_id=uuid.UUID(int=7)),
                ctx,
            )
    finally:
        await engine.dispose()
    after = await signals(database)
    assert after["crimemap_ingest_failures_24h"] == before["crimemap_ingest_failures_24h"] + 2
    assert after["crimemap_ai_run_failures_24h"] == before["crimemap_ai_run_failures_24h"] + 1
    assert after["crimemap_security_exports_max_per_actor_1h"] >= 5
