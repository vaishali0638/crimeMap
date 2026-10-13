"""HTTP layer: request IDs, client IP, rate limiting, security headers, RFC 9457 problem details.

Unhandled exceptions are caught inside the middleware so that even a 500 carries the request ID and
security headers, and never leaks exception text.
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from ipaddress import IPv4Network, IPv6Network, ip_address
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from crimemap_api.ratelimit import RateLimiter

log = logging.getLogger(__name__)

ERROR_BASE = "https://crimemap.local/errors"
PROBLEM_JSON = "application/problem+json"
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}

_STATUS_TYPES = {
    400: ("invalid-request", "Invalid request"),
    401: ("unauthenticated", "Authentication required"),
    403: ("forbidden", "Forbidden"),
    404: ("not-found", "Not found"),
    405: ("method-not-allowed", "Method not allowed"),
    409: ("conflict", "Conflict"),
    413: ("payload-too-large", "Payload too large"),
    415: ("unsupported-media-type", "Unsupported media type"),
    422: ("validation-error", "Validation error"),
    429: ("rate-limited", "Too many requests"),
    500: ("internal-error", "Internal error"),
    503: ("service-unavailable", "Service unavailable"),
}


class ProblemError(Exception):
    """Raise to return a specific RFC 9457 problem response."""

    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(slug)
        self.status, self.slug, self.title, self.detail = status, slug, title, detail
        self.headers = headers or {}


def problem(
    status: int,
    *,
    slug: str | None = None,
    title: str | None = None,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    default_slug, default_title = _STATUS_TYPES.get(status, ("error", "Error"))
    body: dict[str, Any] = {
        "type": f"{ERROR_BASE}/{slug or default_slug}",
        "title": title or default_title,
        "status": status,
        "request_id": request_id_var.get(),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def _valid_request_id(value: str | None) -> str | None:
    if not value or len(value) > 64:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def client_ip(request: Request, trusted_proxies: Sequence[IPv4Network | IPv6Network]) -> str:
    """Peer address, or the last untrusted X-Forwarded-For hop if the peer is a trusted proxy."""
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_ip in net for net in trusted_proxies):
        return peer
    forwarded = [
        h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()
    ]
    for hop in reversed(forwarded):
        try:
            hop_ip = ip_address(hop)
        except ValueError:
            return peer
        if not any(hop_ip in net for net in trusted_proxies):
            return str(hop_ip)
    return peer


def install_http_layer(
    app: FastAPI,
    *,
    rate_limiter: RateLimiter,
    ip_limit_per_minute: int,
    trusted_proxies: Sequence[IPv4Network | IPv6Network] = (),
    hsts: bool = False,
    on_unhandled_error: Callable[[Request, BaseException], Awaitable[None]] | None = None,
    on_response: Callable[[Request, int, float], None] | None = None,
) -> None:
    headers = dict(SECURITY_HEADERS)
    if hsts:
        headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

    @app.middleware("http")
    async def _http_layer(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        rid = _valid_request_id(request.headers.get("x-request-id")) or str(uuid.uuid4())
        token = request_id_var.set(rid)
        request.state.request_id = rid
        request.state.client_ip = client_ip(request, trusted_proxies)
        try:
            allowed, retry_after = await rate_limiter.hit(
                f"ip:{request.state.client_ip}", ip_limit_per_minute, 60
            )
            if not allowed:
                response: Response = problem(429, headers={"Retry-After": str(retry_after)})
            else:
                try:
                    response = await call_next(request)
                except Exception as exc:
                    log.exception("unhandled_error", extra={"request_id": rid})
                    if on_unhandled_error is not None:
                        try:
                            await on_unhandled_error(request, exc)
                        except Exception:
                            log.exception("unhandled_error_hook_failed", extra={"request_id": rid})
                    response = problem(500)
            response.headers.update(headers)
            response.headers["X-Request-ID"] = rid
            if on_response is not None:
                on_response(request, response.status_code, time.perf_counter() - started)
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem(exc.status_code, headers=dict(exc.headers or {}) or None)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back: only field path and message.
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return problem(422, extra={"errors": errors})

    @app.exception_handler(ProblemError)
    async def _problem_exc(request: Request, exc: ProblemError) -> JSONResponse:
        return problem(
            exc.status,
            slug=exc.slug,
            title=exc.title,
            detail=exc.detail,
            headers=exc.headers or None,
        )
