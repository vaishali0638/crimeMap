"""Test helpers: a local signing key standing in for the identity provider."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from crimemap_api.identity.tokens import TokenError, TokenVerifier

ISSUER = "http://localhost:8080/realms/crimemap-demo"
JWKS_URL = "http://keycloak:8080/realms/crimemap-demo/protocol/openid-connect/certs"
AUDIENCE = "crimemap-api"
CLIENT = "crimemap-web"
KID = "test-signing-key"

SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ATTACKER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _resolve(kid: str) -> Any:
    if kid != KID:
        raise TokenError("unknown_key")
    return SIGNING_KEY.public_key()


def local_verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        allowed_clients=frozenset({CLIENT}),
        key_resolver=_resolve,
        mfa_amr_values=frozenset({"otp"}),
    )


def mint(
    sub: str,
    *,
    mfa: bool = True,
    auth_age: timedelta = timedelta(seconds=10),
    key: Any = SIGNING_KEY,
    **extra: Any,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "account"],
        "azp": CLIENT,
        "sub": sub,
        "typ": "Bearer",
        "iat": int(now.timestamp()) - 5,
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "auth_time": int((now - auth_age).timestamp()),
        "amr": ["pwd", "otp"] if mfa else ["pwd"],
        "sid": "test-session",
        "preferred_username": f"user-{sub[:8]}",
        **extra,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
