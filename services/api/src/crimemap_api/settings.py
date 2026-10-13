"""Application settings, read from ``CRIMEMAP_*`` environment variables.

Secrets are ``SecretStr`` so they never appear in ``repr`` or logs. Unsafe combinations are rejected
at startup — in particular, PRODUCTION cannot run with an insecure (http) identity provider or with
the OpenAPI document exposed.
"""

from ipaddress import IPv4Network, IPv6Network
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["DEMO", "SANDBOX", "PRODUCTION"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRIMEMAP_", extra="forbid")

    environment: Environment = "DEMO"

    database_url: SecretStr = Field(description="asyncpg URL for the crimemap_app role")
    redis_url: SecretStr

    oidc_issuer: str = Field(description="Expected `iss` claim (as seen by browsers)")
    oidc_jwks_url: str = Field(description="JWKS endpoint (may use an internal host name)")
    oidc_audience: str = "crimemap-api"
    oidc_allowed_clients: list[str] = ["crimemap-web"]
    allow_insecure_oidc: bool = False
    mfa_amr_values: list[str] = ["otp"]

    # Clients whose X-Forwarded-For header is trusted (the BFF / reverse proxy).
    trusted_proxies: list[IPv4Network | IPv6Network] = []

    rate_limit_ip_per_minute: int = Field(default=300, ge=1)
    rate_limit_user_per_minute: int = Field(default=600, ge=1)
    rate_limit_auth_failures_per_minute: int = Field(default=20, ge=1)

    openapi_enabled: bool | None = None  # default: on outside PRODUCTION

    # Bearer token for the Prometheus scrape of /metrics. Unset: /metrics answers 404.
    metrics_token: SecretStr | None = None

    @field_validator("metrics_token", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _check_safety(self) -> Self:
        http_issuer = self.oidc_issuer.startswith("http://") or self.oidc_jwks_url.startswith(
            "http://"
        )
        if self.environment == "PRODUCTION":
            if self.allow_insecure_oidc:
                raise ValueError("PRODUCTION forbids allow_insecure_oidc")
            if http_issuer:
                raise ValueError("PRODUCTION requires an https issuer and JWKS URL")
            if self.openapi_enabled:
                raise ValueError("PRODUCTION forbids exposing the OpenAPI document")
        elif http_issuer and not self.allow_insecure_oidc:
            raise ValueError("http issuer requires allow_insecure_oidc=true (DEMO/SANDBOX only)")
        if self.metrics_token is not None and len(self.metrics_token.get_secret_value()) < 32:
            raise ValueError("metrics_token must be at least 32 characters")
        if self.openapi_enabled is None:
            self.openapi_enabled = self.environment != "PRODUCTION"
        return self
