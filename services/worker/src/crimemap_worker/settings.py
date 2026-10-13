"""Worker settings (``CRIMEMAP_WORKER_*`` and shared ``CRIMEMAP_*`` variables)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRIMEMAP_WORKER_", extra="ignore")

    environment: Literal["DEMO", "SANDBOX", "PRODUCTION"] = "DEMO"
    database_url: SecretStr  # crimemap_worker role (asyncpg URL)
    redis_url: SecretStr
    keycloak_base_url: str  # internal URL, e.g. http://keycloak:8080
    keycloak_realm: str = "crimemap-demo"
    keycloak_client_id: str = "crimemap-audit-sync"
    keycloak_client_secret: SecretStr
    idp_poll_seconds: int = 60
    # Overlap re-reads a little history each poll; duplicates are discarded by the import table.
    idp_overlap_ms: int = 120_000
    source_health_seconds: int = 300  # integration-contracts §4: every 5 minutes
    # Scheduled ingestion: signed batch files arrive under <inbox_dir>/<source code>/. Unset = off.
    inbox_dir: Path | None = None
    inbox_poll_seconds: int = 300
    demo_signing_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CRIMEMAP_WORKER_DEMO_SIGNING_KEY", "CRIMEMAP_DEMO_SIGNING_KEY"
        ),
    )
