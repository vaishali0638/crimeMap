"""Application factory.

uvicorn crimemap_api.main:build --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crimemap_api import metrics
from crimemap_api.db import make_engine
from crimemap_api.deps import (
    Requirement,
    Services,
    assert_routes_declared,
    install_handlers,
    route_requirements,
    unhandled_error_auditor,
)
from crimemap_api.http import install_http_layer
from crimemap_api.identity.jwks import JwksCache
from crimemap_api.identity.tokens import TokenVerifier
from crimemap_api.logging_config import configure_logging
from crimemap_api.ratelimit import RateLimiter, RedisRateLimiter
from crimemap_api.routes import (
    ai,
    analytics,
    audit,
    auth,
    dashboard,
    dq,
    geography,
    health,
    incidents,
    integrations,
    map,
    opsrecords,
    reports,
    reviewqueue,
    search,
    users,
    workflow,
)
from crimemap_api.settings import Settings
from crimemap_api.startup import assert_no_demo_data


def create_app(
    settings: Settings,
    *,
    verifier: TokenVerifier | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    engine = make_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    http_client: httpx.AsyncClient | None = None
    jwks: JwksCache | None = None
    if verifier is None:
        http_client = httpx.AsyncClient(follow_redirects=False)
        jwks = JwksCache(settings.oidc_jwks_url, client=http_client)
        verifier = TokenVerifier(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            allowed_clients=frozenset(settings.oidc_allowed_clients),
            key_resolver=jwks.get,
            mfa_amr_values=frozenset(settings.mfa_amr_values),
        )

    redis: Redis | None = None
    if rate_limiter is None:
        redis = Redis.from_url(settings.redis_url.get_secret_value(), socket_timeout=1.0)
        rate_limiter = RedisRateLimiter(redis)

    services = Services(
        settings=settings,
        engine=engine,
        sessions=sessions,
        verifier=verifier,
        rate_limiter=rate_limiter,
        jwks=jwks,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await assert_no_demo_data(engine, settings.environment)
        if jwks is not None:
            await jwks.refresh_if_due()  # tolerate an unavailable IdP at startup
        yield
        await engine.dispose()
        if http_client is not None:
            await http_client.aclose()
        if redis is not None:
            await redis.aclose()

    app = FastAPI(
        title="CRIMEMAP API",
        version="0.1.0",
        description="Internal police decision-support API. DEMO / SYNTHETIC DATA.",
        openapi_url="/api/v1/openapi.json" if settings.openapi_enabled else None,
        servers=[
            {"url": "/", "description": "Same origin (reached through the BFF or reverse proxy)"}
        ],
        docs_url=None,  # Swagger/Redoc UIs load third-party scripts; not served
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.services = services
    app.state.engine = engine
    registry = metrics.Registry()
    app.state.metrics = registry

    install_http_layer(
        app,
        rate_limiter=rate_limiter,
        ip_limit_per_minute=settings.rate_limit_ip_per_minute,
        trusted_proxies=settings.trusted_proxies,
        hsts=settings.environment == "PRODUCTION",
        on_unhandled_error=unhandled_error_auditor(services),
        on_response=lambda request, status, seconds: registry.observe(
            request.method, metrics.route_label(request), status, seconds
        ),
    )
    install_handlers(app, services)
    for module in (
        health,
        auth,
        users,
        audit,
        geography,
        dq,
        integrations,
        map,
        incidents,
        search,
        opsrecords,
        reviewqueue,
        dashboard,
        analytics,
        reports,
        workflow,
        ai,
        metrics,
    ):
        app.include_router(module.router)
    assert_routes_declared(app)
    _document_security(app)
    return app


def _document_security(app: FastAPI) -> None:
    """Add bearer auth and each operation's CRIMEMAP permission to the OpenAPI document."""
    default_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = default_openapi()
        schema.setdefault("components", {})["securitySchemes"] = {
            "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
        for key, requirement in route_requirements(app).items():
            method, path = key.split(" ", 1)
            operation = schema["paths"].get(path, {}).get(method.lower())
            if operation is None:
                continue
            if isinstance(requirement, Requirement):
                operation["security"] = [{"BearerAuth": []}]
                operation["x-crimemap-permission"] = requirement.permission or "authenticated"
            else:
                operation["security"] = []
                operation["x-crimemap-permission"] = "public"
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def build() -> FastAPI:
    configure_logging()
    return create_app(Settings())
