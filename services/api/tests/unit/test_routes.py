"""Every API route declares an authorisation requirement; undeclared routes stop startup."""

import pytest
from fastapi import APIRouter

from crimemap_api.deps import (
    AUTHENTICATED,
    PUBLIC,
    Requirement,
    assert_routes_declared,
    route_requirements,
)
from crimemap_api.main import create_app
from crimemap_api.ratelimit import InMemoryRateLimiter
from crimemap_api.settings import Settings

from ..support import ISSUER, JWKS_URL, local_verifier

EXPECTED = {
    "GET /api/v1/health": "PUBLIC",
    "GET /api/v1/ready": "PUBLIC",
    # Phase 8: Prometheus scrape; its own bearer token, checked in the handler (404 when unset).
    "GET /metrics": "PUBLIC",
    "GET /api/v1/auth/me": None,
    "POST /api/v1/auth/login-events": None,
    "GET /api/v1/users": "admin.users",
    "GET /api/v1/users/{user_id}/role-grants": "admin.users",
    "POST /api/v1/users/{user_id}/role-grants": "admin.users",
    "POST /api/v1/users/{user_id}/role-grants/{grant_id}/revocations": "admin.users",
    "GET /api/v1/roles": "admin.users",
    "GET /api/v1/permissions": "admin.users",
    "GET /api/v1/audit/events": "audit.read",
    "GET /api/v1/audit/verification": "audit.read",
    "GET /api/v1/regions": None,
    "GET /api/v1/stations": None,
    "GET /api/v1/beats": None,
    "GET /api/v1/boundaries": None,
    "GET /api/v1/boundaries/{unit_code}/versions": "lineage.read",
    # The approval slot decides the permission (admin.config or dq.manage), checked in the handler.
    "POST /api/v1/boundaries/{boundary_id}/approvals": None,
    "GET /api/v1/dq/issues": "dq.manage",
    "GET /api/v1/dq/summary": "dq.manage",
    "PATCH /api/v1/dq/issues/{issue_id}": "dq.manage",
    "GET /api/v1/integrations/sources": "lineage.read",
    "GET /api/v1/integrations/sources/{code}": "lineage.read",
    "GET /api/v1/integrations/reconciliation": "lineage.read",
    "GET /api/v1/data-freshness": None,
    # Phase 3 (Home Map). None = authenticated; the handler checks the permission that applies
    # (points vs aggregate, summary/sensitive scope) against the requested territory.
    "GET /api/v1/filters/metadata": None,
    "GET /api/v1/map/features": None,
    "GET /api/v1/map/operational-records": "map.points.read",
    "GET /api/v1/incidents/{incident_id}/summary": "incident.summary.read",
    "GET /api/v1/incidents/{incident_id}": "incident.detail.read",
    "POST /api/v1/incidents/{incident_id}/data-issues": "opsrecord.create",
    "GET /api/v1/search": None,
    "POST /api/v1/operational-records": "opsrecord.create",
    "GET /api/v1/operational-records": None,
    "POST /api/v1/operational-records/{record_id}/reviews": "opsrecord.approve",
    "POST /api/v1/notes": "opsrecord.create",
    "GET /api/v1/notes": "opsrecord.create",
    "GET /api/v1/review-queue/summary": None,
    # Phase 4 (PY Dashboard)
    "GET /api/v1/dashboard/stations": "map.aggregate.read",
    "GET /api/v1/dashboard/stations/{station_code}": "map.aggregate.read",
    "GET /api/v1/dashboard/trends/{board}": "map.aggregate.read",
    # Phase 5 (Analytics Workbench, exports). Reports: the handler checks the export permission
    # for the analysis's region and a fresh sign-in.
    "GET /api/v1/analytics/overview": "analytics.read",
    "GET /api/v1/analytics/location": "analytics.read",
    "GET /api/v1/analytics/case-trends": "analytics.read",
    "GET /api/v1/analytics/comparison": "analytics.compare",
    "GET /api/v1/analytics/data-quality": "analytics.read",
    "GET /api/v1/analytics/civic": "analytics.read",
    "POST /api/v1/evidence-snapshots": "analytics.read",
    "GET /api/v1/evidence-snapshots/{snapshot_id}": "analytics.read",
    "POST /api/v1/pattern-reviews": "pattern_review.create",
    "GET /api/v1/pattern-reviews": "pattern_review.create",
    "POST /api/v1/reports": None,
    "GET /api/v1/reports": None,
    "GET /api/v1/reports/{report_id}/download": None,
    # Phase 6 (workflow). Reads (None) are checked per item: territory plus any workflow permission.
    "GET /api/v1/pattern-reviews/{review_id}": None,
    "PATCH /api/v1/pattern-reviews/{review_id}": "pattern_review.create",
    "POST /api/v1/pattern-reviews/{review_id}/submissions": "pattern_review.create",
    "POST /api/v1/pattern-reviews/{review_id}/decisions": "pattern_review.decide",
    "GET /api/v1/actions/assignees": None,
    "POST /api/v1/actions": "action.propose",
    "POST /api/v1/actions/{action_id}/approvals": "action.approve",
    "POST /api/v1/actions/{action_id}/completions": "action.complete",
    "GET /api/v1/actions/{action_id}": None,
    "POST /api/v1/outcomes": "outcome.review",
    "PATCH /api/v1/outcomes/{outcome_id}": "outcome.review",
    "POST /api/v1/outcomes/{outcome_id}/finalisations": "outcome.review",
    "GET /api/v1/outcomes/{outcome_id}": None,
    "GET /api/v1/review-queue": None,
    "GET /api/v1/workflow-history": None,
    # Phase 7 (AI). Per-insight territory and the steward's DQ-only rule are checked in the handler.
    "GET /api/v1/ai/insights": "ai.insight.read",
    "GET /api/v1/ai/insights/{insight_id}": "ai.insight.read",
    "POST /api/v1/ai/insights/{insight_id}/reviews": "ai.insight.review",
    "GET /api/v1/ai/runs/{run_id}": "ai.insight.read",
    "GET /api/v1/ai/models": "ai.model.read",
    "POST /api/v1/ai/models/{model_id}/switch": "ai.config",
    "POST /api/v1/ai/settings": "ai.config",
    "POST /api/v1/ai/briefings": "ai.insight.review",
    "POST /api/v1/ai/copilot/queries": "ai.insight.read",
}


def app():  # type: ignore[no-untyped-def]
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/x",  # type: ignore[arg-type]
        redis_url="redis://localhost/0",  # type: ignore[arg-type]
        oidc_issuer=ISSUER,
        oidc_jwks_url=JWKS_URL,
        allow_insecure_oidc=True,
    )
    return create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())


def describe(req: object) -> str | None:
    if isinstance(req, Requirement):
        return req.permission
    return "PUBLIC"


def test_routes_and_their_requirements_are_exactly_as_expected() -> None:
    actual = {k: describe(v) for k, v in route_requirements(app()).items()}
    assert actual == EXPECTED


def test_authenticated_requirement_is_the_shared_instance() -> None:
    reqs = route_requirements(app())
    assert reqs["GET /api/v1/auth/me"] is AUTHENTICATED


def test_route_without_requirement_stops_startup() -> None:
    application = app()
    rogue = APIRouter()

    @rogue.get("/api/v1/rogue")
    def rogue_route() -> dict[str, str]:
        return {}

    application.include_router(rogue)
    with pytest.raises(RuntimeError, match="rogue"):
        assert_routes_declared(application)


def test_app_with_no_routes_is_refused() -> None:
    from fastapi import FastAPI

    with pytest.raises(RuntimeError, match="no API routes"):
        assert_routes_declared(FastAPI(openapi_url=None))


def test_public_marker_is_recognised() -> None:
    application = app()
    extra = APIRouter()

    @extra.get("/api/v1/also-public", dependencies=[PUBLIC])
    def also_public() -> dict[str, str]:
        return {}

    application.include_router(extra)
    assert_routes_declared(application)


def test_openapi_documents_auth_and_permission_per_operation() -> None:
    spec = app().openapi()
    assert spec["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    users = spec["paths"]["/api/v1/users"]["get"]
    assert users["security"] == [{"BearerAuth": []}]
    assert users["x-crimemap-permission"] == "admin.users"
    health = spec["paths"]["/api/v1/health"]["get"]
    assert (health["security"], health["x-crimemap-permission"]) == ([], "public")
    assert spec["paths"]["/api/v1/auth/me"]["get"]["x-crimemap-permission"] == "authenticated"


def test_openapi_schema_names_are_unique_across_modules() -> None:
    """Two response models with the same class name get module-prefixed OpenAPI names, which breaks
    the generated web types (found in Phase 3: three classes called SummaryOut)."""
    from crimemap_api.main import create_app
    from crimemap_api.ratelimit import InMemoryRateLimiter
    from crimemap_api.settings import Settings

    from ..support import local_verifier

    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/x",  # type: ignore[arg-type]
        redis_url="redis://localhost/0",  # type: ignore[arg-type]
        oidc_issuer="http://localhost:8080/realms/crimemap-demo",
        oidc_jwks_url="http://localhost:8080/certs",
        allow_insecure_oidc=True,
    )
    app = create_app(settings, verifier=local_verifier(), rate_limiter=InMemoryRateLimiter())
    names = app.openapi()["components"]["schemas"].keys()
    assert not [n for n in names if n.startswith("crimemap_api__")]
