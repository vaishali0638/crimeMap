"""Cache of the identity provider's signing keys (JWKS), refreshed on key rotation.

Refreshes are rate-limited so a flood of tokens with unknown ``kid`` values cannot turn the API into
a request amplifier against the identity provider. A failed refresh keeps the previous keys.
"""

import json
import logging
import time
from typing import Any

import httpx
from jwt.algorithms import RSAAlgorithm

from crimemap_api.identity.tokens import TokenError

log = logging.getLogger(__name__)


class JwksCache:
    def __init__(
        self, url: str, *, client: httpx.AsyncClient, min_refresh_seconds: float = 60.0
    ) -> None:
        self._url = url
        self._client = client
        self._min_refresh = min_refresh_seconds
        self._keys: dict[str, Any] = {}
        self._last_attempt: float | None = None

    async def refresh(self) -> None:
        """Fetch the key set now. Raises on failure; existing keys are kept."""
        self._last_attempt = time.monotonic()
        response = await self._client.get(self._url, timeout=5.0)
        response.raise_for_status()
        keys: dict[str, Any] = {}
        for entry in response.json().get("keys", []):
            if entry.get("kty") != "RSA" or entry.get("use", "sig") != "sig" or "kid" not in entry:
                continue
            keys[entry["kid"]] = RSAAlgorithm.from_jwk(json.dumps(entry))
        self._keys = keys

    async def refresh_if_due(self) -> bool:
        """Refresh unless one was attempted recently. Returns True if keys were refreshed."""
        if (
            self._last_attempt is not None
            and time.monotonic() - self._last_attempt < self._min_refresh
        ):
            return False
        try:
            await self.refresh()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("jwks_refresh_failed", extra={"error": type(exc).__name__})
            return False
        return True

    def get(self, kid: str) -> Any:
        try:
            return self._keys[kid]
        except KeyError:
            raise TokenError("unknown_key") from None
