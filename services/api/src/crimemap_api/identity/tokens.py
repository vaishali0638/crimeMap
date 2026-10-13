"""Access-token verification for tokens issued by the OIDC provider (Keycloak in development).

Only RS256 is accepted, the key is chosen by ``kid`` from the provider's JWKS, and issuer, audience,
authorised party (``azp``), token type, and time claims are all checked. Errors carry a short reason
code only — never token content.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

ALLOWED_ALGORITHMS = ("RS256",)
LEEWAY = timedelta(seconds=30)


class TokenError(Exception):
    """Token rejected; ``str(err)`` is a short machine reason, never token content."""


@dataclass(frozen=True)
class TokenClaims:
    subject: str
    mfa: bool
    auth_time: datetime | None
    session_id: str | None
    username: str | None
    name: str | None
    email: str | None
    amr: tuple[str, ...]


def _ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, int | float):
        raise TokenError("malformed")
    return datetime.fromtimestamp(value, UTC)


class TokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        allowed_clients: frozenset[str],
        key_resolver: Callable[[str], Any],
        mfa_amr_values: frozenset[str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._allowed_clients = allowed_clients
        self._key_resolver = key_resolver
        self._mfa_amr_values = mfa_amr_values
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(self, token: str) -> TokenClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise TokenError("malformed") from exc
        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise TokenError("unsupported_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenError("malformed")
        key = self._key_resolver(kid)  # raises TokenError("unknown_key")

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
                # Time claims are checked below against the injectable clock.
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.InvalidAudienceError as exc:
            raise TokenError("wrong_audience") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenError("wrong_issuer") from exc
        except jwt.InvalidSignatureError as exc:
            raise TokenError("invalid_signature") from exc
        except jwt.MissingRequiredClaimError as exc:
            raise TokenError("missing_claim") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenError("invalid_token") from exc

        now = self._clock()
        exp, iat, nbf = _ts(payload["exp"]), _ts(payload["iat"]), _ts(payload.get("nbf"))
        if exp is None or iat is None:
            raise TokenError("missing_claim")
        if exp <= now - LEEWAY:
            raise TokenError("expired")
        if iat > now + LEEWAY or (nbf is not None and nbf > now + LEEWAY):
            raise TokenError("not_yet_valid")
        if payload.get("typ") != "Bearer":
            raise TokenError("wrong_token_type")
        if payload.get("azp") not in self._allowed_clients:
            raise TokenError("wrong_client")

        subject = payload["sub"]
        if not isinstance(subject, str) or not subject:
            raise TokenError("missing_claim")
        raw_amr = payload.get("amr", [])
        amr = tuple(v for v in raw_amr if isinstance(v, str)) if isinstance(raw_amr, list) else ()

        def text(name: str) -> str | None:
            value = payload.get(name)
            return value if isinstance(value, str) else None

        return TokenClaims(
            subject=subject,
            mfa=bool(self._mfa_amr_values.intersection(amr)),
            auth_time=_ts(payload.get("auth_time")),
            session_id=text("sid"),
            username=text("preferred_username"),
            name=text("name"),
            email=text("email"),
            amr=amr,
        )
