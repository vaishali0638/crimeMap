"""Minimal Keycloak admin client for reading user events.

Authenticates with the client-credentials grant as a dedicated service account
(``crimemap-audit-sync``) that holds only the realm-management ``view-events`` role.
"""

from typing import Any

import httpx

PAGE_SIZE = 200


class KeycloakEventsClient:
    def __init__(
        self,
        *,
        base_url: str,
        realm: str,
        client_id: str,
        client_secret: str,
        http: httpx.AsyncClient,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    async def _token(self) -> str:
        response = await self._http.post(
            f"{self._base}/realms/{self._realm}/protocol/openid-connect/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            timeout=10.0,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str):
            raise RuntimeError("token endpoint returned no access_token")
        return token

    async def events_since(self, since_ms: int, *, max_pages: int = 20) -> list[dict[str, Any]]:
        """Events at or after ``since_ms`` (epoch millis), oldest first."""
        token = await self._token()
        events: list[dict[str, Any]] = []
        for page in range(max_pages):
            response = await self._http.get(
                f"{self._base}/admin/realms/{self._realm}/events",
                params={
                    "dateFrom": str(since_ms),
                    "direction": "asc",
                    "first": page * PAGE_SIZE,
                    "max": PAGE_SIZE,
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise RuntimeError("unexpected events response")
            events.extend(e for e in batch if isinstance(e, dict))
            if len(batch) < PAGE_SIZE:
                break
        return events
