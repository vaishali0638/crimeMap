"""The JWKS cache resolves signing keys by kid and refreshes safely on key rotation."""

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from crimemap_api.identity.jwks import JwksCache
from crimemap_api.identity.tokens import TokenError

URL = "http://keycloak:8080/realms/crimemap-demo/protocol/openid-connect/certs"


def jwk(kid: str, use: str = "sig") -> dict[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
    data: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(key))
    data.update({"kid": kid, "use": use, "alg": "RS256"})
    return data


class FakeIdp:
    def __init__(self, *keys: dict[str, Any]) -> None:
        self.keys = list(keys)
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        assert str(request.url) == URL
        return httpx.Response(200, json={"keys": self.keys})


def cache(idp: FakeIdp, min_refresh_seconds: float = 60.0) -> JwksCache:
    client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))
    return JwksCache(URL, client=client, min_refresh_seconds=min_refresh_seconds)


async def test_resolves_key_after_refresh() -> None:
    c = cache(FakeIdp(jwk("a")))
    await c.refresh()
    assert c.get("a") is not None


async def test_unknown_kid_raises_token_error() -> None:
    c = cache(FakeIdp(jwk("a")))
    await c.refresh()
    with pytest.raises(TokenError, match="unknown_key"):
        c.get("b")


async def test_encryption_keys_are_ignored() -> None:
    c = cache(FakeIdp(jwk("enc", use="enc")))
    await c.refresh()
    with pytest.raises(TokenError, match="unknown_key"):
        c.get("enc")


async def test_refresh_for_rotated_key_is_rate_limited() -> None:
    idp = FakeIdp(jwk("a"))
    c = cache(idp, min_refresh_seconds=60)
    await c.refresh()
    assert await c.refresh_if_due() is False  # too soon: no second request
    assert idp.calls == 1


async def test_rotated_key_becomes_available_after_due_refresh() -> None:
    idp = FakeIdp(jwk("a"))
    c = cache(idp, min_refresh_seconds=0)
    await c.refresh()
    idp.keys.append(jwk("b"))
    assert await c.refresh_if_due() is True
    assert c.get("b") is not None


async def test_failed_refresh_keeps_previous_keys() -> None:
    good = FakeIdp(jwk("a"))
    c = cache(good, min_refresh_seconds=0)
    await c.refresh()
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert await c.refresh_if_due() is False
    assert c.get("a") is not None
