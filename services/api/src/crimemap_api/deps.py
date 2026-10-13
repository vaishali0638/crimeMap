"""Request pipeline: authentication, authorisation requirements, and failure auditing.

Every route must declare exactly what it needs with one of:
    dependencies=[PUBLIC]                      no authentication (health/readiness only)
    ctx: Ctx = Depends(AUTHENTICATED)          any verified user
    ctx: Ctx = Depends(Requirement("perm"))    a catalogue permission
``create_app`` refuses to start if any route declares none of these (deny by default).
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from crimemap_api.audit.events import AuditContext, AuditEvent, write_independent
from crimemap_api.db import UnitOfWork
from crimemap_api.http import ProblemError, problem
from crimemap_api.identity import store
from crimemap_api.identity.jwks import JwksCache
from crimemap_api.identity.store import UserRow
from crimemap_api.identity.tokens import TokenClaims, TokenError, TokenVerifier
from crimemap_api.policy.catalogue import Purpose
from crimemap_api.policy.engine import (
    REAUTH_MAX_AGE,
    Decision,
    DenyReason,
    Subject,
    Target,
    authorize,
)
from crimemap_api.ratelimit import RateLimiter
from crimemap_api.settings import Settings

log = logging.getLogger(__name__)
security_log = logging.getLogger("crimemap.security")


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    verifier: TokenVerifier
    rate_limiter: RateLimiter
    jwks: JwksCache | None = None

    async def verify(self, token: str) -> TokenClaims:
        try:
            return self.verifier.verify(token)
        except TokenError as exc:
            # A new signing key after rotation: refresh once (rate-limited inside JwksCache).
            if (
                str(exc) == "unknown_key"
                and self.jwks is not None
                and await self.jwks.refresh_if_due()
            ):
                return self.verifier.verify(token)
            raise


class AuthnFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthzDenied(Exception):
    def __init__(self, ctx: "RequestContext", permission: str, reason: DenyReason) -> None:
        super().__init__(reason)
        self.ctx, self.permission, self.reason = ctx, permission, reason


_DENY_PROBLEMS: dict[DenyReason, tuple[str, str]] = {
    DenyReason.NO_PERMISSION: ("forbidden", "Forbidden"),
    DenyReason.MFA_REQUIRED: ("mfa-required", "Multi-factor authentication required"),
    DenyReason.OUT_OF_SCOPE: ("out-of-scope", "Outside your authorised scope"),
    DenyReason.PURPOSE_REQUIRED: ("purpose-required", "A declared purpose is required"),
    DenyReason.PURPOSE_NOT_PERMITTED: ("purpose-not-permitted", "Purpose not permitted"),
    DenyReason.REAUTH_REQUIRED: ("reauthentication-required", "Please sign in again"),
}


@dataclass
class RequestContext:
    services: Services
    claims: TokenClaims
    user: UserRow
    subject: Subject
    audit_ctx: AuditContext
    decision: Decision | None = field(default=None)

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(sorted({g.role.value for g in self.subject.grants}))

    def event(self, action: str, result: str = "ALLOW", **kw: Any) -> AuditEvent:
        return AuditEvent(
            action=action,
            result=result,  # type: ignore[arg-type]
            actor_user_id=self.user.id,
            actor_subject=self.claims.subject,
            actor_roles=self.roles,
            **kw,
        )

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.services.sessions, self.audit_ctx, self.subject)

    def require(
        self, permission: str, *, target: Target | None = None, purpose: Purpose | None = None
    ) -> Decision:
        decision = authorize(self.subject, permission, target=target, purpose=purpose)
        if not decision.allowed:
            raise AuthzDenied(self, permission, decision.reason or DenyReason.NO_PERMISSION)
        return decision

    def require_fresh_auth(self, permission: str) -> None:
        auth_time = self.subject.auth_time
        if auth_time is None or datetime.now(UTC) - auth_time > REAUTH_MAX_AGE:
            raise AuthzDenied(self, permission, DenyReason.REAUTH_REQUIRED)

    async def deny(self, event: AuditEvent, status: int, slug: str, title: str) -> ProblemError:
        """Audit a business-rule denial independently and return the problem to raise."""
        await write_independent(self.services.engine, event, self.audit_ctx)
        return ProblemError(status, slug, title)


def _audit_context(request: Request, settings: Settings) -> AuditContext:
    return AuditContext(
        request_id=getattr(request.state, "request_id", None),
        environment=settings.environment,
        ip_address=getattr(request.state, "client_ip", None),
        user_agent=request.headers.get("user-agent"),
    )


async def get_context(request: Request) -> RequestContext:
    services: Services = request.app.state.services
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if not header:
        raise AuthnFailure("missing_token")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthnFailure("malformed")
    try:
        claims = await services.verify(token.strip())
    except TokenError as exc:
        raise AuthnFailure(str(exc)) from None

    allowed, retry_after = await services.rate_limiter.hit(
        f"user:{claims.subject}", services.settings.rate_limit_user_per_minute, 60
    )
    if not allowed:
        raise ProblemError(
            429, "rate-limited", "Too many requests", headers={"Retry-After": str(retry_after)}
        )

    async with services.sessions() as session, session.begin():
        user = await store.provision_user(
            session, claims, environment=services.settings.environment
        )
        grants = await store.load_role_grants(session, user.id)
    if user.status != "ACTIVE":
        raise AuthnFailure("account_not_active")

    ctx = RequestContext(
        services=services,
        claims=claims,
        user=user,
        subject=Subject(user_id=user.id, grants=grants, mfa=claims.mfa, auth_time=claims.auth_time),
        audit_ctx=_audit_context(request, services.settings),
    )
    request.state.ctx = ctx
    return ctx


class Requirement:
    """Route dependency: authenticated, and (unless ``permission`` is None) holding ``permission``."""

    def __init__(self, permission: str | None) -> None:
        self.permission = permission

    async def __call__(
        self, ctx: Annotated[RequestContext, Depends(get_context)]
    ) -> RequestContext:
        if self.permission is not None:
            ctx.decision = ctx.require(self.permission)
        return ctx

    def __repr__(self) -> str:
        return f"Requirement({self.permission!r})"


AUTHENTICATED = Requirement(None)


def public_endpoint() -> None:
    """Marker dependency for endpoints that need no authentication."""


PUBLIC = Depends(public_endpoint)


def _requirements(dependant: Dependant) -> list[object]:
    found: list[object] = []
    for dep in dependant.dependencies:
        if isinstance(dep.call, Requirement) or dep.call is public_endpoint:
            found.append(dep.call)
        found.extend(_requirements(dep))
    return found


def route_requirements(app: FastAPI) -> dict[str, object | None]:
    """Map 'METHOD path' to the declared requirement (None if the route declares nothing).

    Uses FastAPI's public ``iter_route_contexts`` because, since FastAPI 0.143, included routers
    are not flattened into ``app.routes``.
    """
    result: dict[str, object | None] = {}
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            reqs = _requirements(route.dependant)
            for method in sorted(context.methods or route.methods or set()):
                result[f"{method} {context.path}"] = reqs[0] if reqs else None
    return result


def assert_routes_declared(app: FastAPI) -> None:
    requirements = route_requirements(app)
    if not requirements:
        raise RuntimeError("no API routes discovered; refusing to start without the route check")
    missing = [k for k, v in requirements.items() if v is None]
    if missing:
        raise RuntimeError(f"routes without an authorisation requirement: {missing}")


def install_handlers(app: FastAPI, services: Services) -> None:
    @app.exception_handler(AuthnFailure)
    async def _authn(request: Request, exc: AuthnFailure) -> JSONResponse:
        ctx = _audit_context(request, services.settings)
        allowed, retry_after = await services.rate_limiter.hit(
            f"authfail:{ctx.ip_address}", services.settings.rate_limit_auth_failures_per_minute, 60
        )
        if not allowed:
            return problem(429, headers={"Retry-After": str(retry_after)})
        await write_independent(
            services.engine,
            AuditEvent(action="AUTHN_FAILED", result="DENY", reason=exc.reason),
            ctx,
        )
        challenge = "Bearer" if exc.reason == "missing_token" else 'Bearer error="invalid_token"'
        return problem(401, headers={"WWW-Authenticate": challenge})

    @app.exception_handler(AuthzDenied)
    async def _authz(request: Request, exc: AuthzDenied) -> JSONResponse:
        await write_independent(
            services.engine,
            exc.ctx.event(
                "AUTHZ_DENIED",
                "DENY",
                reason=exc.reason.value,
                after_meta={"permission": exc.permission},
            ),
            exc.ctx.audit_ctx,
        )
        slug, title = _DENY_PROBLEMS[exc.reason]
        security_log.warning(
            "authorization_denied",
            extra={
                "permission": exc.permission,
                "reason": exc.reason.value,
                "user_id": str(exc.ctx.user.id),
            },
        )
        return problem(403, slug=slug, title=title)


def unhandled_error_auditor(services: Services):  # type: ignore[no-untyped-def]
    async def _audit(request: Request, exc: BaseException) -> None:
        ctx: RequestContext | None = getattr(request.state, "ctx", None)
        event = (
            ctx.event(
                "REQUEST_ERROR", "ERROR", reason=type(exc).__name__, object_type=request.url.path
            )
            if ctx
            else AuditEvent(
                action="REQUEST_ERROR",
                result="ERROR",
                reason=type(exc).__name__,
                object_type=request.url.path,
            )
        )
        await write_independent(services.engine, event, _audit_context(request, services.settings))

    return _audit


Ctx = RequestContext
