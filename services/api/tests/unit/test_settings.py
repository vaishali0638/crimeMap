"""Configuration refuses unsafe combinations, especially in PRODUCTION."""

from typing import Any

import pytest
from pydantic import ValidationError

from crimemap_api.settings import Settings

BASE: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://crimemap_app:x@localhost:5432/crimemap",
    "redis_url": "redis://localhost:6379/0",
    "oidc_issuer": "http://localhost:8080/realms/crimemap-demo",
    "oidc_jwks_url": "http://keycloak:8080/realms/crimemap-demo/protocol/openid-connect/certs",
    "allow_insecure_oidc": True,
}


def make(**overrides: Any) -> Settings:
    return Settings(**{**BASE, **overrides})  # type: ignore[arg-type]


def test_demo_defaults() -> None:
    s = make()
    assert s.environment == "DEMO"
    assert s.oidc_audience == "crimemap-api"
    assert s.mfa_amr_values == ["otp"]
    assert s.openapi_enabled is True


def test_http_issuer_requires_explicit_insecure_flag() -> None:
    with pytest.raises(ValidationError, match="http issuer requires allow_insecure_oidc=true"):
        make(allow_insecure_oidc=False)


def test_production_rejects_insecure_oidc() -> None:
    with pytest.raises(ValidationError, match="PRODUCTION forbids allow_insecure_oidc"):
        make(
            environment="PRODUCTION",
            oidc_issuer="https://idp.example.gov.in/realms/crimemap",
            oidc_jwks_url="https://idp.example.gov.in/realms/crimemap/protocol/openid-connect/certs",
            allow_insecure_oidc=True,
            openapi_enabled=False,
        )


def test_production_rejects_http_issuer() -> None:
    with pytest.raises(ValidationError, match="PRODUCTION requires an https issuer"):
        make(environment="PRODUCTION", allow_insecure_oidc=False, openapi_enabled=False)


def test_production_disables_openapi_by_default() -> None:
    s = make(
        environment="PRODUCTION",
        oidc_issuer="https://idp.example.gov.in/realms/crimemap",
        oidc_jwks_url="https://idp.example.gov.in/realms/crimemap/protocol/openid-connect/certs",
        allow_insecure_oidc=False,
    )
    assert s.openapi_enabled is False


def test_secrets_are_not_shown_in_repr() -> None:
    assert "crimemap_app:x@" not in repr(make())


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Input should be 'DEMO', 'SANDBOX' or 'PRODUCTION'"):
        make(environment="STAGING")
