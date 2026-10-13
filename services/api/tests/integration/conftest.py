"""Ephemeral PostgreSQL + PostGIS for integration tests (testcontainers).

The container uses the project's own image (infrastructure/docker/postgres), so the init script that
creates the three database roles is exactly the one used by Docker Compose. Migrations run as the
migrator role; the API under test connects as crimemap_app.
"""

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from crimemap_worker.ingest import IngestResult, ingest_batch_file
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from crimemap_api.main import create_app
from crimemap_api.policy.catalogue import Role
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

from ..support import ISSUER, JWKS_URL, local_verifier

API_DIR = Path(__file__).resolve().parents[2]
REPO = API_DIR.parents[1]
IMAGE = "crimemap-postgres:dev"
DB = "crimemap"


@dataclass(frozen=True)
class Database:
    host: str
    port: int
    superuser_password: str
    migrator_password: str
    app_password: str
    worker_password: str
    ai_password: str
    container: str = ""  # Docker container ID (backup/restore tests run tools inside it)

    def dsn(self, role: str, password: str, *, driver: str = "") -> str:
        scheme = f"postgresql+{driver}" if driver else "postgresql"
        return f"{scheme}://{role}:{password}@{self.host}:{self.port}/{DB}"

    @property
    def superuser(self) -> str:
        return self.dsn("postgres", self.superuser_password)

    @property
    def migrator(self) -> str:
        return self.dsn("crimemap_migrator", self.migrator_password)

    @property
    def app(self) -> str:
        return self.dsn("crimemap_app", self.app_password)

    @property
    def app_async(self) -> str:
        return self.dsn("crimemap_app", self.app_password, driver="asyncpg")

    @property
    def worker(self) -> str:
        return self.dsn("crimemap_worker", self.worker_password)

    @property
    def worker_async(self) -> str:
        return self.dsn("crimemap_worker", self.worker_password, driver="asyncpg")

    @property
    def ai(self) -> str:
        return self.dsn("crimemap_ai", self.ai_password)

    @property
    def ai_async(self) -> str:
        return self.dsn("crimemap_ai", self.ai_password, driver="asyncpg")


def _ensure_image() -> None:
    present = (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
        ).returncode
        == 0
    )
    if not present:
        subprocess.run(
            ["docker", "build", "-t", IMAGE, str(REPO / "infrastructure" / "docker" / "postgres")],
            check=True,
        )


def alembic_config(migrator_url: str) -> Config:
    os.environ["CRIMEMAP_MIGRATOR_DATABASE_URL"] = migrator_url
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    return cfg


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    _ensure_image()
    passwords = {k: secrets.token_urlsafe(18) for k in ("su", "mig", "app", "wrk", "ai")}
    container = (
        PostgresContainer(IMAGE, username="postgres", password=passwords["su"], dbname="postgres")
        .with_env("CRIMEMAP_DB_NAME", DB)
        .with_env("CRIMEMAP_MIGRATOR_PASSWORD", passwords["mig"])
        .with_env("CRIMEMAP_APP_PASSWORD", passwords["app"])
        .with_env("CRIMEMAP_WORKER_PASSWORD", passwords["wrk"])
        .with_env("CRIMEMAP_AI_PASSWORD", passwords["ai"])
    )
    with container:
        db = Database(
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            superuser_password=passwords["su"],
            migrator_password=passwords["mig"],
            app_password=passwords["app"],
            worker_password=passwords["wrk"],
            ai_password=passwords["ai"],
            container=container.get_wrapped_container().id,
        )
        cfg = alembic_config(db.migrator)
        command.upgrade(cfg, "head")
        from crimemap_api.cli import sync_catalogue

        sync_catalogue(db.migrator)
        yield db


@pytest.fixture
def su(database: Database) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database.superuser, autocommit=True) as conn:
        yield conn


@pytest.fixture
def app_conn(database: Database) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database.app, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def seeded(database: Database) -> Database:
    """Synthetic boundaries and demo identities (Phase 2), seeded once per test session."""
    from crimemap_api.cli import seed_boundaries, seed_demo_identities

    seed_boundaries(database.migrator, "DEMO")
    seed_demo_identities(database.migrator, "DEMO")
    return database


# --- Phase 2: one synthetic dataset, generated and ingested once per test session -----------------

KEY = b"k" * 32  # test-only signing key
N = 3000


@dataclass
class Ingested:
    database: Database
    manifest: dict[str, Any]
    files: list[Path]
    results: list[IngestResult]
    legacy_files: list[Path]


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def ingest_files(
    database: Database, files: list[Path], environment: str = "DEMO"
) -> list[IngestResult]:
    engine = create_async_engine(database.worker_async)
    try:
        return [await ingest_batch_file(engine, f, KEY, environment=environment) for f in files]
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def ingested(seeded: Database, tmp_path_factory: pytest.TempPathFactory) -> Ingested:
    from crimemap_api.cli import seed_offence_map
    from crimemap_api.demo.generator import GeneratorConfig, generate, write_batches

    seed_offence_map(seeded.migrator, "DEMO")
    out = tmp_path_factory.mktemp("batches")
    data = generate(GeneratorConfig(records=N, seed=7))
    files = write_batches(data, out / "main", KEY, batch_size=1000, extracted_at=datetime.now(UTC))
    legacy = write_batches(
        generate(GeneratorConfig(records=20, seed=8, source_system="DEMO_SYNTHETIC_LEGACY")),
        out / "legacy",
        KEY,
        batch_size=100,
        extracted_at=datetime.now(UTC) - timedelta(days=10),
        source_system="DEMO_SYNTHETIC_LEGACY",
    )
    results = run(ingest_files(seeded, files + legacy))
    return Ingested(seeded, data.manifest, files, results, legacy)


# --- API client and user fixtures shared by the API test modules ----------------------------------


@pytest.fixture
async def api(database: Database) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=database.app_async,  # type: ignore[arg-type]
        redis_url="redis://unused:6379/0",  # type: ignore[arg-type]
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
        allow_insecure_oidc=True,
        rate_limit_auth_failures_per_minute=1000,
    )
    app = create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    await app.state.engine.dispose()


@pytest.fixture
def make_user(database: Database) -> Callable[..., tuple[str, str]]:
    """Create a user with optional grant; returns (idp_subject, user_id)."""

    def _make(
        role: Role | None = None, regions: tuple[str, ...] = (), stations: tuple[str, ...] = ()
    ) -> tuple[str, str]:
        sub = str(uuid.uuid4())
        with psycopg.connect(database.migrator, autocommit=True) as conn:
            row = conn.execute(
                "INSERT INTO core.app_user (idp_subject, username) VALUES (%s, %s) RETURNING id",
                [sub, f"user-{sub[:8]}"],
            ).fetchone()
            assert row is not None
            if role is not None:
                conn.execute(
                    "INSERT INTO core.user_role_grant (user_id, role_code, scope_region_ids, "
                    "scope_station_ids, grant_reason) SELECT %s, %s, "
                    "coalesce((SELECT array_agg(id) FROM core.organizational_unit "
                    "          WHERE unit_type = 'REGION' AND code = ANY(%s)), '{}'), "
                    "coalesce((SELECT array_agg(id) FROM core.organizational_unit "
                    "          WHERE unit_type = 'STATION' AND code = ANY(%s)), '{}'), 'test'",
                    [row[0], role.value, list(regions), list(stations)],
                )
        return sub, str(row[0])

    return _make


@pytest.fixture
def audit_rows(database: Database) -> Callable[[str], list[dict[str, Any]]]:
    def _rows(request_id: str) -> list[dict[str, Any]]:
        with psycopg.connect(database.superuser) as conn:
            cur = conn.execute(
                "SELECT action, result, reason, actor_user_id::text, actor_subject, object_type, "
                "before_meta, after_meta, purpose FROM audit.audit_event WHERE request_id = %s ORDER BY seq",
                [request_id],
            )
            names = [d.name for d in cur.description or []]
            return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]

    return _rows
