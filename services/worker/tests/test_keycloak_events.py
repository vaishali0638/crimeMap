"""Keycloak events become CRIMEMAP audit events without leaking what users typed."""

import hashlib

import pytest

from crimemap_worker.keycloak_events import event_key, map_event

LOGIN_ERROR = {
    "id": "ev-1",
    "time": 1791729000123,
    "type": "LOGIN_ERROR",
    "realmId": "crimemap-demo",
    "clientId": "crimemap-web",
    "userId": "fa44f4b2-f0fc-4f40-b6a7-3a2bbd893403",
    "ipAddress": "203.0.113.9",
    "error": "invalid_user_credentials",
    "details": {"username": "demo.analyst", "auth_method": "openid-connect"},
}


def test_failed_login_maps_to_login_failed_denial() -> None:
    mapped = map_event(LOGIN_ERROR)
    assert mapped is not None
    event, ip = mapped
    assert (event.action, event.result, event.reason) == (
        "LOGIN_FAILED",
        "DENY",
        "invalid_user_credentials",
    )
    assert event.actor_subject == LOGIN_ERROR["userId"]
    assert ip == "203.0.113.9"
    assert event.after_meta is not None
    assert event.after_meta["source"] == "IDP"
    assert event.after_meta["idp_event_time_ms"] == 1791729000123


def test_attempted_username_is_hashed_never_stored() -> None:
    event, _ = map_event(LOGIN_ERROR)  # type: ignore[misc]
    meta = event.after_meta or {}
    assert "demo.analyst" not in repr(event)
    digest = hashlib.sha256(b"demo.analyst").hexdigest()[:16]
    assert meta["attempted_username_sha256_16"] == digest


def test_unknown_user_failed_login_has_no_actor() -> None:
    raw = {**LOGIN_ERROR, "userId": None, "error": "user_not_found"}
    event, _ = map_event(raw)  # type: ignore[misc]
    assert event.actor_subject is None
    assert event.reason == "user_not_found"


@pytest.mark.parametrize(
    ("kc_type", "action", "result"),
    [
        ("LOGIN", "IDP_LOGIN", "ALLOW"),
        ("LOGOUT", "LOGOUT", "ALLOW"),
        ("UPDATE_TOTP", "MFA_ENROLLED", "ALLOW"),
        ("REMOVE_TOTP", "MFA_REMOVED", "ALLOW"),
        ("CODE_TO_TOKEN_ERROR", "TOKEN_EXCHANGE_FAILED", "DENY"),
    ],
)
def test_security_relevant_types_are_mapped(kc_type: str, action: str, result: str) -> None:
    event, _ = map_event({**LOGIN_ERROR, "type": kc_type})  # type: ignore[misc]
    assert (event.action, event.result) == (action, result)


def test_irrelevant_types_are_ignored() -> None:
    assert map_event({**LOGIN_ERROR, "type": "INTROSPECT_TOKEN"}) is None


def test_untrusted_reason_text_is_bounded() -> None:
    event, _ = map_event({**LOGIN_ERROR, "error": "x" * 5000})  # type: ignore[misc]
    assert event.reason is not None
    assert len(event.reason) <= 64


def test_event_key_uses_keycloak_id() -> None:
    assert event_key(LOGIN_ERROR) == "kc:ev-1"


def test_event_key_without_id_is_a_stable_content_hash() -> None:
    raw = {k: v for k, v in LOGIN_ERROR.items() if k != "id"}
    assert event_key(raw) == event_key(dict(reversed(list(raw.items()))))
    assert event_key(raw).startswith("kch:")
