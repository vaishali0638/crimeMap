"""Liveness and readiness. Public, rate-limited, and deliberately uninformative."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from crimemap_api.deps import PUBLIC, Services
from crimemap_api.settings import Environment

router = APIRouter(prefix="/api/v1", tags=["health"])


class HealthOut(BaseModel):
    status: str
    environment: Environment


class ReadyOut(BaseModel):
    status: str
    checks: dict[str, bool]


@router.get("/health", dependencies=[PUBLIC])
async def health(request: Request) -> HealthOut:
    services: Services = request.app.state.services
    return HealthOut(status="ok", environment=services.settings.environment)


@router.get("/ready", dependencies=[PUBLIC], response_model=ReadyOut)
async def ready(request: Request) -> JSONResponse:
    services: Services = request.app.state.services

    async def database() -> bool:
        try:
            async with asyncio.timeout(2), services.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def cache() -> bool:
        try:
            async with asyncio.timeout(2):
                return await services.rate_limiter.ping()
        except Exception:
            return False

    checks = {"database": await database(), "cache": await cache()}
    ok = all(checks.values())
    body = ReadyOut(status="ready" if ok else "not_ready", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)
