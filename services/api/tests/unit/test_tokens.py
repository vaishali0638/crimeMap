"""Access-token verification rejects every malformed, forged, or misdirected token."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from crimemap_api.identity.tokens import TokenError, TokenVerifier

ISSUER = "http://localhost:8080/realms/crimemap-demo"
AUDIENCE = "crimemap-api"
CLIENT = "crimemap-web"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "test-kid"


def _keys(kid: str) -> Any:
    if kid != KID:
        raise TokenError("unknown_key")
    return _KEY.public_key()


def verifier() -> TokenVerifier:
    return TokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        allowed_clients=frozenset({CLIENT}),
        key_resolver=_keys,
        mfa_amr_values=frozenset({"otp"}),
        clock=lambda: NOW,
    )


def claims(**overrides: Any) -> dict[str, Any]:
    base = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "account"],
        "azp": CLIENT,
        "sub": "6f1c8a52-0d8e-4f0e-9a51-1c2b3d4e5f60",
        "typ": "Bearer",
        "iat": int(NOW.timestamp()) - 10,
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
        "auth_time": int(NOW.timestamp()) - 10,
        "amr": ["pwd", "otp"],
        "sid": "session-1",
        "preferred_username": "demo.analyst",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def sign(payload: dict[str, Any], key: Any = _KEY, kid: str = KID, alg: str = "RS256") -> str:
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


def test_valid_token_yields_claims_with_mfa() -> None:
    c = verifier().verify(sign(claims()))
    assert c.subject == "6f1c8a52-0d8e-4f0e-9a51-1c2b3d4e5f60"
    assert c.mfa is True
    assert c.auth_time == NOW - timedelta(seconds=10)
    assert c.session_id == "session-1"


def test_token_without_otp_is_not_mfa() -> None:
    assert verifier().verify(sign(claims(amr=["pwd"]))).mfa is False


def test_token_without_amr_is_not_mfa() -> None:
    assert verifier().verify(sign(claims(amr=None))).mfa is False


def test_expired_token_is_rejected() -> None:
    expired = claims(exp=int((NOW - timedelta(minutes=1)).timestamp()))
    with pytest.raises(TokenError, match="expired"):
        verifier().verify(sign(expired))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("aud", ["account"], "wrong_audience"),
        ("iss", "http://evil.example/realms/crimemap-demo", "wrong_issuer"),
        ("azp", "some-other-client", "wrong_client"),
        ("typ", "ID", "wrong_token_type"),
    ],
)
def test_misdirected_token_is_rejected(field: str, value: Any, reason: str) -> None:
    with pytest.raises(TokenError, match=reason):
        verifier().verify(sign(claims(**{field: value})))


@pytest.mark.parametrize("missing", ["sub", "exp", "iat"])
def test_token_missing_required_claim_is_rejected(missing: str) -> None:
    payload = claims()
    del payload[missing]
    with pytest.raises(TokenError):
        verifier().verify(sign(payload))


def test_token_signed_by_another_key_is_rejected() -> None:
    with pytest.raises(TokenError, match="invalid_signature"):
        verifier().verify(sign(claims(), key=_OTHER_KEY))


def test_token_with_unknown_key_id_is_rejected() -> None:
    with pytest.raises(TokenError, match="unknown_key"):
        verifier().verify(sign(claims(), kid="other"))


def test_unsigned_alg_none_token_is_rejected() -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": KID}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(claims()).encode()).rstrip(b"=")
    with pytest.raises(TokenError):
        verifier().verify(f"{header.decode()}.{body.decode()}.")


def test_hmac_token_using_public_key_as_secret_is_rejected() -> None:
    public_pem = _KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    with pytest.raises(TokenError):
        verifier().verify(_forge_hs256(public_pem))


def _forge_hs256(secret: bytes) -> str:
    """Build an HS256 token by hand; PyJWT refuses PEM-looking HMAC keys (as it should)."""
    import hashlib
    import hmac

    def b64(data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data).rstrip(b"=")

    signing_input = (
        b64(json.dumps({"alg": "HS256", "kid": KID}).encode())
        + b"."
        + b64(json.dumps(claims()).encode())
    )
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + b64(sig)).decode()


def test_garbage_is_rejected() -> None:
    with pytest.raises(TokenError, match="malformed"):
        verifier().verify("not-a-jwt")
