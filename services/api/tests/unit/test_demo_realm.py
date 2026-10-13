"""The Keycloak demo realm and the CRIMEMAP demo identities stay identical, and the realm keeps its
security settings (mandatory TOTP with amr references, PKCE, short tokens, no secrets in git)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from crimemap_api.demo_identities import DEMO_IDENTITIES

REALM_FILE = (
    Path(__file__).resolve().parents[4]
    / "infrastructure"
    / "docker"
    / "keycloak"
    / "crimemap-demo-realm.json"
)


@pytest.fixture(scope="module")
def realm() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    return data


def client(realm: dict[str, Any], client_id: str) -> dict[str, Any]:
    return next(c for c in realm["clients"] if c["clientId"] == client_id)


def test_realm_users_match_demo_identities(realm: dict[str, Any]) -> None:
    people = {u["id"]: u["username"] for u in realm["users"] if "serviceAccountClientId" not in u}
    assert people == {i.idp_subject: i.username for i in DEMO_IDENTITIES}


def test_no_secret_values_are_committed(realm: dict[str, Any]) -> None:
    text = REALM_FILE.read_text(encoding="utf-8")
    for c in realm["clients"]:
        assert re.fullmatch(r"\$\{[A-Z_]+\}", c["secret"])
    for u in realm["users"]:
        for cred in u.get("credentials", []):
            assert re.fullmatch(r"\$\{[A-Z_]+\}", cred["value"])
    assert "BEGIN" not in text  # no embedded keys


def test_browser_flow_requires_password_and_otp_with_amr(realm: dict[str, Any]) -> None:
    assert realm["browserFlow"] == "crimemap browser"
    forms = next(f for f in realm["authenticationFlows"] if f["alias"] == "crimemap browser forms")
    steps = {e["authenticator"]: e for e in forms["authenticationExecutions"]}
    assert steps["auth-username-password-form"]["requirement"] == "REQUIRED"
    assert steps["auth-otp-form"]["requirement"] == "REQUIRED"
    refs = {
        c["alias"]: c["config"]["default.reference.value"] for c in realm["authenticatorConfig"]
    }
    assert refs[steps["auth-otp-form"]["authenticatorConfig"]] == "otp"
    assert refs[steps["auth-username-password-form"]["authenticatorConfig"]] == "pwd"


def test_web_client_is_confidential_pkce_code_flow_only(realm: dict[str, Any]) -> None:
    web = client(realm, "crimemap-web")
    assert web["publicClient"] is False
    assert web["standardFlowEnabled"] is True
    assert web["implicitFlowEnabled"] is False
    assert web["directAccessGrantsEnabled"] is False
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert web["webOrigins"] == []
    assert all("*" not in uri for uri in web["redirectUris"])


def test_access_tokens_carry_audience_and_amr(realm: dict[str, Any]) -> None:
    mappers = {m["protocolMapper"]: m for m in client(realm, "crimemap-web")["protocolMappers"]}
    assert mappers["oidc-audience-mapper"]["config"]["included.custom.audience"] == "crimemap-api"
    assert mappers["oidc-amr-mapper"]["config"]["access.token.claim"] == "true"


def test_session_and_token_lifetimes(realm: dict[str, Any]) -> None:
    assert realm["accessTokenLifespan"] <= 300
    assert realm["ssoSessionIdleTimeout"] <= 900
    assert realm["revokeRefreshToken"] is True
    assert realm["refreshTokenMaxReuse"] == 0
    assert realm["bruteForceProtected"] is True


def test_audit_sync_account_can_only_view_events(realm: dict[str, Any]) -> None:
    sync = client(realm, "crimemap-audit-sync")
    assert sync["serviceAccountsEnabled"] is True
    assert sync["standardFlowEnabled"] is False
    account = next(
        u for u in realm["users"] if u.get("serviceAccountClientId") == "crimemap-audit-sync"
    )
    assert account["clientRoles"] == {"realm-management": ["view-events"]}


def test_failed_logins_are_recorded_by_the_idp(realm: dict[str, Any]) -> None:
    assert realm["eventsEnabled"] is True
    assert "LOGIN_ERROR" in realm["enabledEventTypes"]


def test_amr_references_stay_valid_for_the_whole_session(realm: dict[str, Any]) -> None:
    """Keycloak drops an amr value once auth_time + default.reference.maxAge has passed, and the
    default maxAge is 0 — so without this setting tokens carry an empty amr (found in Phase 1)."""
    for config in realm["authenticatorConfig"]:
        assert (
            int(config["config"].get("default.reference.maxAge", "0"))
            >= realm["ssoSessionMaxLifespan"]
        )
