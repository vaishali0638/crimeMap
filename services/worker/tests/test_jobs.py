"""The scheduler runs ingestion, source health and reconciliation, with exponential-backoff retries."""

import importlib
import sys

import pytest


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CRIMEMAP_WORKER_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
    monkeypatch.setenv("CRIMEMAP_WORKER_REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("CRIMEMAP_WORKER_KEYCLOAK_BASE_URL", "http://keycloak:8080")
    monkeypatch.setenv("CRIMEMAP_WORKER_KEYCLOAK_CLIENT_SECRET", "test-only")
    sys.modules.pop("crimemap_worker.jobs", None)
    return importlib.import_module("crimemap_worker.jobs")


@pytest.mark.parametrize("name", ["ingest_inbox_batches", "check_sources", "reconcile_yesterday"])
def test_phase2_jobs_retry_with_exponential_backoff(jobs, name: str) -> None:  # type: ignore[no-untyped-def]
    actor = getattr(jobs, name)
    assert actor.options["max_retries"] == jobs.MAX_RETRIES > 0
    assert actor.options["min_backoff"] < actor.options["max_backoff"]


def test_inbox_ingestion_is_off_without_an_inbox(jobs) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    assert jobs.settings.inbox_dir is None
    assert asyncio.run(jobs._ingest_inbox()) == {}


def test_signing_key_is_read_from_the_shared_variable(
    monkeypatch: pytest.MonkeyPatch, jobs
) -> None:  # type: ignore[no-untyped-def]
    from crimemap_worker.settings import WorkerSettings

    monkeypatch.setenv("CRIMEMAP_DEMO_SIGNING_KEY", "x" * 32)
    key = WorkerSettings().demo_signing_key
    assert key is not None
    assert key.get_secret_value() == "x" * 32
