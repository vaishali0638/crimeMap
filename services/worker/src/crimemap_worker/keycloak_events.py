"""Map Keycloak user events to CRIMEMAP audit events.

Failed logins happen at the identity provider, so CRIMEMAP imports them from Keycloak's event store
(Phase 1 acceptance: "audit logs record ... failed login"). Values from Keycloak are untrusted text:
reasons are bounded, and the username someone typed is stored only as a truncated hash (it may be a
mistyped password or another person's identifier).
"""

import hashlib
import json
import re
from typing import Any

from crimemap_api.audit.events import AuditEvent, Result

# Keycloak type -> (CRIMEMAP action, result)
EVENT_TYPES: dict[str, tuple[str, Result]] = {
    "LOGIN": ("IDP_LOGIN", "ALLOW"),
    "LOGIN_ERROR": ("LOGIN_FAILED", "DENY"),
    "LOGOUT": ("LOGOUT", "ALLOW"),
    "LOGOUT_ERROR": ("LOGOUT_FAILED", "DENY"),
    "CODE_TO_TOKEN_ERROR": ("TOKEN_EXCHANGE_FAILED", "DENY"),
    "REFRESH_TOKEN_ERROR": ("TOKEN_REFRESH_FAILED", "DENY"),
    "UPDATE_TOTP": ("MFA_ENROLLED", "ALLOW"),
    "REMOVE_TOTP": ("MFA_REMOVED", "ALLOW"),
    "UPDATE_PASSWORD": ("PASSWORD_CHANGED", "ALLOW"),
}

_SAFE_TEXT = re.compile(r"[^A-Za-z0-9_.:-]")


def _bounded(value: Any, limit: int = 64) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _SAFE_TEXT.sub("_", value)[:limit]


def map_event(raw: dict[str, Any]) -> tuple[AuditEvent, str | None] | None:
    """Return (audit event, client IP) or None if the event type is not security-relevant."""
    mapping = EVENT_TYPES.get(str(raw.get("type")))
    if mapping is None:
        return None
    action, result = mapping
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    meta: dict[str, Any] = {
        "source": "IDP",
        "idp_event_type": _bounded(raw.get("type")),
        "idp_event_time_ms": raw.get("time") if isinstance(raw.get("time"), int) else None,
        "client_id": _bounded(raw.get("clientId")),
    }
    username = details.get("username") if isinstance(details, dict) else None
    if isinstance(username, str) and username:
        meta["attempted_username_sha256_16"] = hashlib.sha256(username.encode()).hexdigest()[:16]
    event = AuditEvent(
        action=action,
        result=result,
        reason=_bounded(raw.get("error")),
        object_type="idp_session",
        actor_subject=_bounded(raw.get("userId"), 128),
        after_meta={k: v for k, v in meta.items() if v is not None},
    )
    ip = raw.get("ipAddress") if isinstance(raw.get("ipAddress"), str) else None
    return event, ip


def event_key(raw: dict[str, Any]) -> str:
    """Stable de-duplication key: Keycloak's event id, or a canonical content hash."""
    event_id = raw.get("id")
    if isinstance(event_id, str) and event_id:
        return f"kc:{event_id}"
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return "kch:" + hashlib.sha256(canonical.encode()).hexdigest()
