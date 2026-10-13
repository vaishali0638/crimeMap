"""AI insights, review, model registry, kill switches, briefings and the copilot (Phase 7, ADR-037).

Every insight is labelled "AI-assisted; analyst review required." and carries its evidence and
limitations (the database requires both). Analysts validate or dismiss it with a note (audited;
the database allows only that change, once, by the session user). Nothing here lets AI create an
action, export data or change a record: the API has no such path, and the detectors' database role
has no such grant.

Who sees what:
- ``ai.insight.read`` over the insight's region or station; a data steward's grant covers the
  data-quality insight types only (DQ anomaly, duplicate, geocode).
- Records an insight refers to (duplicates, geocoding) are named only if the caller may see them.
- Insights of a switched-off model, or while AI is switched off, are kept but not offered for
  review.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.ai import copilot
from crimemap_api.deps import AUTHENTICATED, Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.catalogue import Role
from crimemap_api.policy.engine import PermissionScope, Subject, Target, authorize, permission_scope
from crimemap_api.routes.analytics import PERSON_TERMS
from crimemap_api.routes.search import looks_personal

router = APIRouter(prefix="/api/v1", tags=["ai"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]
ReadCtx = Annotated[Ctx, Depends(Requirement("ai.insight.read"))]
ReviewCtx = Annotated[Ctx, Depends(Requirement("ai.insight.review"))]
ModelCtx = Annotated[Ctx, Depends(Requirement("ai.model.read"))]
ConfigCtx = Annotated[Ctx, Depends(Requirement("ai.config"))]

LABEL = "AI-assisted; analyst review required."
DQ_TYPES = frozenset({"DQ_ANOMALY", "DUPLICATE", "GEOCODE"})
InsightType = Literal["DQ_ANOMALY", "DUPLICATE", "GEOCODE", "EMERGING_PATTERN", "CLUSTER"]
State = Literal["PENDING", "VALIDATED", "DISMISSED"]


def insight_scope(
    ctx: Ctx, insight_type: str, permission: str = "ai.insight.read"
) -> PermissionScope:
    """A steward's AI grant covers data-quality insights only (role matrix note)."""
    if insight_type in DQ_TYPES:
        return permission_scope(ctx.subject, permission)
    others = tuple(g for g in ctx.subject.grants if g.role is not Role.DATA_STEWARD)
    return permission_scope(
        Subject(
            user_id=ctx.subject.user_id,
            grants=others,
            mfa=ctx.subject.mfa,
            auth_time=ctx.subject.auth_time,
        ),
        permission,
    )


def _covers(scope: PermissionScope, region_id: UUID, station_id: UUID | None) -> bool:
    return (
        scope.is_global
        or region_id in scope.region_ids
        or (station_id is not None and station_id in scope.station_ids)
    )


def scope_params(ctx: Ctx, permission: str) -> dict[str, Any]:
    """The caller's territory for data-quality (dg/dr/ds) and analytic (ag/ar/as_) insight types, for
    SQL: the row limit then applies to what the caller may see (Phase 7 audit)."""
    dq = insight_scope(ctx, "DQ_ANOMALY", permission)
    an = insight_scope(ctx, "EMERGING_PATTERN", permission)
    return {
        "dg": dq.is_global,
        "dr": list(dq.region_ids),
        "ds": list(dq.station_ids),
        "ag": an.is_global,
        "ar": list(an.region_ids),
        "as_": list(an.station_ids),
    }


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


def may_see(ctx: Ctx, row: Any, permission: str = "ai.insight.read") -> bool:
    return _covers(insight_scope(ctx, row.insight_type, permission), row.region_id, row.station_id)


async def _flag(s: AsyncSession, key: str) -> bool:
    return bool(
        (
            await s.execute(
                text("SELECT enabled FROM core.feature_flag WHERE key = :k"), {"k": key}
            )
        ).scalar_one_or_none()
    )


async def _not_found(ctx: Ctx, object_type: str, object_id: Any) -> ProblemError:
    return await ctx.deny(
        ctx.event(
            "RECORD_VIEW",
            "DENY",
            reason="NOT_FOUND_IN_SCOPE",
            object_type=object_type,
            object_id=str(object_id),
        ),
        404,
        "not-found",
        "Not found",
    )


# --- insights ------------------------------------------------------------------------------------------

_INSIGHT = text(
    """
SELECT
i.id, i.run_id, i.insight_type, i.region_id, i.station_id, i.geography, ST_AsGeoJSON(i.geometry, 6) AS geometry,
i.window_start, i.window_end, i.category_label, i.evidence, i.baseline, i.data_quality_score, i.exclusions, i.confidence,
i.confidence_label, i.calibration_note, i.explanation, i.limitations, i.alternative_explanations, i.subject_incident_ids,
i.review_required, i.state, i.decision_note, i.decided_at, i.created_at, d.username AS decider,
m.id AS model_id, m.code AS model_code, m.version AS model_version, m.name AS model_name, m.is_enabled AS model_enabled,
r.code AS region_code, s.code AS station_code

FROM core.ai_insight i
JOIN core.model_registry m ON m.id = i.model_id
JOIN core.organizational_unit r ON r.id = i.region_id
LEFT JOIN core.organizational_unit s ON s.id = i.station_id
LEFT JOIN core.app_user d ON d.id = i.decided_by
WHERE i.id = :id
"""
)
_LIST = text(
    """
SELECT
i.id, i.run_id, i.insight_type, i.region_id, i.station_id, i.geography, ST_AsGeoJSON(i.geometry, 6) AS geometry,
i.window_start, i.window_end, i.category_label, i.evidence, i.baseline, i.data_quality_score, i.exclusions, i.confidence,
i.confidence_label, i.calibration_note, i.explanation, i.limitations, i.alternative_explanations, i.subject_incident_ids,
i.review_required, i.state, i.decision_note, i.decided_at, i.created_at, d.username AS decider,
m.id AS model_id, m.code AS model_code, m.version AS model_version, m.name AS model_name, m.is_enabled AS model_enabled,
r.code AS region_code, s.code AS station_code

FROM core.ai_insight i
JOIN core.model_registry m ON m.id = i.model_id
JOIN core.organizational_unit r ON r.id = i.region_id
LEFT JOIN core.organizational_unit s ON s.id = i.station_id
LEFT JOIN core.app_user d ON d.id = i.decided_by
WHERE (CAST(:state AS text) IS NULL OR i.state = :state)
  AND (CAST(:type AS text) IS NULL OR i.insight_type = :type)
  AND (CAST(:region AS text) IS NULL OR r.code = :region)
  AND ((i.insight_type IN ('DQ_ANOMALY', 'DUPLICATE', 'GEOCODE')
        AND (:dg OR i.region_id = ANY (:dr) OR i.station_id = ANY (:ds)))
    OR (i.insight_type IN ('EMERGING_PATTERN', 'CLUSTER')
        AND (:ag OR i.region_id = ANY (:ar) OR i.station_id = ANY (:as_))))
  AND (NOT :pending_only OR m.is_enabled)
ORDER BY CASE i.confidence_label WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, i.created_at DESC, i.id
LIMIT :limit
"""
)


class Reference(BaseModel):
    incident_id: UUID
    reference: str | None  # None: the caller may not see this record


class InsightSummary(BaseModel):
    id: UUID
    label: str
    insight_type: str
    model: str  # code@version
    region: str
    station: str | None
    window_start: str
    window_end: str
    category: str
    explanation: str
    confidence: float
    confidence_label: str
    state: str
    created_at: datetime
    reviewable: bool


class InsightHistoryItem(BaseModel):
    at: datetime
    actor: str
    from_state: str | None
    to_state: str
    note: str | None


class InsightCard(InsightSummary):
    """The spec's AI insight card contract."""

    model_name: str
    model_enabled: bool
    ai_enabled: bool  # the global AI switch
    input_snapshot_id: UUID  # the detector run (its parameters, inputs and hash)
    geography: dict[str, Any]
    geometry: dict[str, Any] | None
    evidence: dict[str, Any]
    baseline: dict[str, Any]
    data_quality_score: float
    exclusions: dict[str, Any]
    calibration_note: str
    limitations: list[str]
    alternative_explanations: list[str]
    required_action: str
    references: list[Reference]
    decision_note: str | None
    decided_by: str | None
    decided_at: datetime | None
    history: list[InsightHistoryItem]


def _summary(row: Any, *, reviewable: bool) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": LABEL,
        "insight_type": row.insight_type,
        "model": f"{row.model_code}@{row.model_version}",
        "region": row.region_code,
        "station": row.station_code,
        "window_start": row.window_start.isoformat(),
        "window_end": row.window_end.isoformat(),
        "category": row.category_label,
        "explanation": row.explanation,
        "confidence": float(row.confidence),
        "confidence_label": row.confidence_label,
        "state": row.state,
        "created_at": row.created_at,
        "reviewable": reviewable,
    }


def _reviewable(ctx: Ctx, row: Any, ai_on: bool) -> bool:
    return (
        ai_on
        and row.model_enabled
        and row.state == "PENDING"
        and may_see(ctx, row, "ai.insight.review")
    )


class InsightList(BaseModel):
    data: list[InsightSummary]
    ai_enabled: bool


@router.get("/ai/insights")
async def list_insights(
    ctx: ReadCtx,
    state: State | None = "PENDING",
    insight_type: Annotated[InsightType | None, Query(alias="type")] = None,
    region: Annotated[str | None, Query(pattern=r"^[A-Z]{2,5}$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> InsightList:
    """Insights the caller may see, most confident first. Pending insights of a switched-off model
    are not listed (they cannot be reviewed)."""
    async with ctx.uow() as uow:
        s = uow.session
        ai_on = await _flag(s, "ai_assistance")
        rows = (
            await s.execute(
                _LIST,
                {
                    "state": state,
                    "type": insight_type,
                    "region": region,
                    **scope_params(ctx, "ai.insight.read"),
                    "pending_only": state == "PENDING",
                    "limit": limit,
                },
            )
        ).all()
        visible = [r for r in rows if may_see(ctx, r)]
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="ai_insight",
                filters={"state": state, "type": insight_type, "region": region},
                after_meta={"count": len(visible)},
            )
        )
        await uow.commit()
    return InsightList(
        data=[
            InsightSummary(**_summary(r, reviewable=_reviewable(ctx, r, ai_on))) for r in visible
        ],
        ai_enabled=ai_on,
    )


async def _load_insight(ctx: Ctx, s: AsyncSession, insight_id: UUID) -> Any:
    row = (await s.execute(_INSIGHT, {"id": insight_id})).one_or_none()
    if row is None or not may_see(ctx, row):
        raise await _not_found(ctx, "ai_insight", insight_id)
    return row


_REFERENCES = text(
    "SELECT id, source_record_reference, region_id, station_id, is_sensitive_category "
    "FROM core.incident_record WHERE id = ANY (:ids)"
)
_REVIEW_HISTORY = text(
    """
SELECT e.occurred_at, u.username, e.before_meta, e.after_meta FROM audit.audit_event e
LEFT JOIN core.app_user u ON u.id = e.actor_user_id
WHERE e.object_type = 'ai_insight' AND e.object_id = :id AND e.action = 'AI_INSIGHT_REVIEW' AND e.result = 'ALLOW'
ORDER BY e.seq
"""
)


async def _card(ctx: Ctx, s: AsyncSession, row: Any, ai_on: bool) -> InsightCard:
    references = []
    if row.subject_incident_ids:
        found = {
            r.id: r
            for r in (await s.execute(_REFERENCES, {"ids": list(row.subject_incident_ids)})).all()
        }
        for incident_id in row.subject_incident_ids:
            r = found.get(
                incident_id
            )  # row-level security hides records outside the caller's territory
            target = Target(region_id=r.region_id, station_id=r.station_id) if r else None
            ok = (
                r is not None
                and authorize(ctx.subject, "incident.summary.read", target=target).allowed
                and (
                    not r.is_sensitive_category
                    or authorize(
                        ctx.subject, "incident.sensitive_category.read", target=target
                    ).allowed
                )
            )
            references.append(
                Reference(
                    incident_id=incident_id,
                    reference=r.source_record_reference if ok and r else None,
                )
            )
    history = [
        InsightHistoryItem(
            at=row.created_at,
            actor=f"AI ({row.model_code}@{row.model_version})",
            from_state=None,
            to_state="PENDING",
            note=None,
        )
    ]
    for h in (await s.execute(_REVIEW_HISTORY, {"id": str(row.id)})).all():
        history.append(
            InsightHistoryItem(
                at=h.occurred_at,
                actor=h.username or "—",
                from_state=(h.before_meta or {}).get("state"),
                to_state=str((h.after_meta or {}).get("state") or "UNKNOWN"),
                note=None,
            )
        )
    if row.decision_note and len(history) > 1:
        history[-1].note = row.decision_note
    return InsightCard(
        **_summary(row, reviewable=_reviewable(ctx, row, ai_on)),
        model_name=row.model_name,
        model_enabled=row.model_enabled,
        ai_enabled=ai_on,
        input_snapshot_id=row.run_id,
        geography=row.geography,
        geometry=json.loads(row.geometry) if row.geometry else None,
        evidence=row.evidence,
        baseline=row.baseline,
        data_quality_score=float(row.data_quality_score),
        exclusions=row.exclusions,
        calibration_note=row.calibration_note,
        limitations=list(row.limitations),
        alternative_explanations=list(row.alternative_explanations),
        required_action=row.review_required,
        references=references,
        decision_note=row.decision_note,
        decided_by=row.decider,
        decided_at=row.decided_at,
        history=history,
    )


@router.get("/ai/insights/{insight_id}")
async def get_insight(ctx: ReadCtx, insight_id: UUID) -> InsightCard:
    async with ctx.uow() as uow:
        s = uow.session
        row = await _load_insight(ctx, s, insight_id)
        card = await _card(ctx, s, row, await _flag(s, "ai_assistance"))
        uow.audit(ctx.event("RECORD_VIEW", object_type="ai_insight", object_id=str(insight_id)))
        await uow.commit()
    return card


def _no_people(value: str) -> str:
    if looks_personal(value) or PERSON_TERMS.search(value):
        raise ValueError(
            "describe places, times and records only: no personal identifiers or references to individuals"
        )
    return value


class InsightReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["VALIDATE", "DISMISS"]
    note: str = Field(min_length=10, max_length=2000)

    _people = field_validator("note")(_no_people)


@router.post("/ai/insights/{insight_id}/reviews")
async def review_insight(ctx: ReviewCtx, insight_id: UUID, body: InsightReviewIn) -> InsightCard:
    """Validate or dismiss an insight, with a note (audited). Validation records the analyst's
    judgement only: it changes no record and creates no action."""
    async with ctx.uow() as uow:
        s = uow.session
        await s.execute(
            text("SELECT 1 FROM core.ai_insight WHERE id = :id FOR UPDATE"), {"id": insight_id}
        )
        row = await _load_insight(ctx, s, insight_id)
        event = {"object_type": "ai_insight", "object_id": str(insight_id)}
        if not may_see(ctx, row, "ai.insight.review"):
            raise await ctx.deny(
                ctx.event("AI_INSIGHT_REVIEW", "DENY", reason="OUT_OF_SCOPE", **event),
                403,
                "forbidden",
                "Forbidden",
            )
        if not await _flag(s, "ai_assistance") or not row.model_enabled:
            raise await ctx.deny(
                ctx.event("AI_INSIGHT_REVIEW", "DENY", reason="AI_DISABLED", **event),
                409,
                "ai-disabled",
                "This model (or AI assistance) is switched off; its insights are not reviewed",
            )
        if row.state != "PENDING":
            raise await ctx.deny(
                ctx.event("AI_INSIGHT_REVIEW", "DENY", reason="INVALID_STATE", **event),
                409,
                "invalid-state-transition",
                "This insight has already been reviewed",
            )
        new = "VALIDATED" if body.decision == "VALIDATE" else "DISMISSED"
        try:
            await s.execute(
                text(
                    "UPDATE core.ai_insight SET state = :st, decision_note = :n, decided_by = :u WHERE id = :id"
                ),
                {"st": new, "n": body.note, "u": ctx.user.id, "id": insight_id},
            )
        except DBAPIError:
            raise await ctx.deny(
                ctx.event("AI_INSIGHT_REVIEW", "DENY", reason="INVALID_STATE", **event),
                409,
                "invalid-state-transition",
                "This insight could not be reviewed; reload and try again",
            ) from None
        uow.audit(
            ctx.event(
                "AI_INSIGHT_REVIEW",
                before_meta={"state": "PENDING"},
                after_meta={
                    "state": new,
                    "type": row.insight_type,
                    "model": f"{row.model_code}@{row.model_version}",
                    "note_length": len(body.note),
                },
                **event,
            )
        )
        row = (await s.execute(_INSIGHT, {"id": insight_id})).one()
        await uow.commit()
    async with ctx.uow() as uow:  # the card's history includes the decision just audited
        card = await _card(ctx, uow.session, row, True)
        await uow.commit()
    return card


# --- runs (input data snapshots) ------------------------------------------------------------------------


class RunOut(BaseModel):
    id: UUID
    model: str
    model_name: str
    region: str
    as_of: str
    parameters: dict[str, Any]
    input_summary: dict[str, Any]
    input_sha256: str | None
    status: str
    insight_count: int
    started_at: datetime
    finished_at: datetime | None


@router.get("/ai/runs/{run_id}")
async def get_run(ctx: ReadCtx, run_id: UUID) -> RunOut:
    """An insight's input snapshot: the run's parameters, input counts and exclusions, and hash."""
    async with ctx.uow() as uow:
        row = (
            await uow.session.execute(
                text(
                    "SELECT a.*, m.code, m.version, m.name, m.insight_type, r.code AS region_code FROM core.ai_run a "
                    "JOIN core.model_registry m ON m.id = a.model_id JOIN core.organizational_unit r ON r.id = a.region_id "
                    "WHERE a.id = :id"
                ),
                {"id": run_id},
            )
        ).one_or_none()
        # The run of any insight the caller may see (a station-scoped reader sees the runs of their
        # station's insights), or a run of a region the caller's AI grant covers.
        visible = row is not None and (
            _covers(insight_scope(ctx, row.insight_type or "EMERGING_PATTERN"), row.region_id, None)
            or any(
                may_see(ctx, i)
                for i in (
                    await uow.session.execute(
                        text(
                            "SELECT insight_type, region_id, station_id FROM core.ai_insight WHERE run_id = :id"
                        ),
                        {"id": run_id},
                    )
                ).all()
            )
        )
        if row is None or not visible:
            raise await _not_found(ctx, "ai_run", run_id)
        uow.audit(ctx.event("RECORD_VIEW", object_type="ai_run", object_id=str(run_id)))
        await uow.commit()
    return RunOut(
        id=row.id,
        model=f"{row.code}@{row.version}",
        model_name=row.name,
        region=row.region_code,
        as_of=row.as_of.isoformat(),
        parameters=row.parameters,
        input_summary=row.input_summary,
        input_sha256=row.input_sha256,
        status=row.status,
        insight_count=row.insight_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


# --- model registry and kill switches -------------------------------------------------------------------


class ModelOut(BaseModel):
    id: UUID
    code: str
    version: str
    name: str
    kind: str
    insight_type: str | None
    description: str
    intended_use: str
    prohibited_uses: list[str]
    features: list[str]
    parameters: dict[str, Any]
    training_data_period: str | None
    evaluation_metrics: dict[str, Any]
    bias_fairness_notes: str
    limitations: list[str]
    owner: str
    approval_status: str
    approval_note: str | None
    rollout_gates: dict[str, Any]
    gates_met: bool | None  # None: no gates apply (rules and statistics)
    is_enabled: bool
    changed_by: str | None
    changed_at: datetime | None
    change_reason: str | None
    rollback_to: str | None  # code@version this model rolls back to (spec: rollback state)


class ModelList(BaseModel):
    data: list[ModelOut]
    ai_assistance: bool
    ai_copilot: bool
    ai_forecasting: bool


_MODELS = text(
    "SELECT m.*, u.username AS changer, rb.code || '@' || rb.version AS rollback_to FROM core.model_registry m "
    "LEFT JOIN core.app_user u ON u.id = m.changed_by LEFT JOIN core.model_registry rb ON rb.id = m.rollback_to_version_id "
    "WHERE (CAST(:id AS uuid) IS NULL OR m.id = :id) ORDER BY m.kind, m.code, m.version"
)


def _model(row: Any) -> ModelOut:
    gates = dict(row.rollout_gates)
    return ModelOut(
        id=row.id,
        code=row.code,
        version=row.version,
        name=row.name,
        kind=row.kind,
        insight_type=row.insight_type,
        description=row.description,
        intended_use=row.intended_use,
        prohibited_uses=list(row.prohibited_uses),
        features=list(row.features),
        parameters=row.parameters,
        training_data_period=row.training_data_period,
        evaluation_metrics=row.evaluation_metrics,
        bias_fairness_notes=row.bias_fairness_notes,
        limitations=list(row.limitations),
        owner=row.owner,
        approval_status=row.approval_status,
        approval_note=row.approval_note,
        rollout_gates=gates,
        gates_met=(bool(gates) and all(v is True for v in gates.values()))
        if row.kind in ("ML", "LLM")
        else None,
        is_enabled=row.is_enabled,
        changed_by=row.changer,
        changed_at=row.changed_at,
        change_reason=row.change_reason,
        rollback_to=row.rollback_to,
    )


@router.get("/ai/models")
async def list_models(ctx: ModelCtx) -> ModelList:
    """The model registry: model cards, approval, rollout gates and switches."""
    async with ctx.uow() as uow:
        s = uow.session
        rows = (await s.execute(_MODELS, {"id": None})).all()
        flags = {k: await _flag(s, k) for k in ("ai_assistance", "ai_copilot", "ai_forecasting")}
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="model_registry", after_meta={"count": len(rows)})
        )
        await uow.commit()
    return ModelList(data=[_model(r) for r in rows], **flags)


class SwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool
    reason: str = Field(min_length=10, max_length=500)


@router.post("/ai/models/{model_id}/switch")
async def switch_model(ctx: ConfigCtx, model_id: UUID, body: SwitchIn) -> ModelOut:
    """A model's kill switch (needs a sign-in in the last 5 minutes). Switching on requires an
    approved model and, for forecasting and language models, every rollout gate (database rule)."""
    ctx.require_fresh_auth("ai.config")
    async with ctx.uow() as uow:
        s = uow.session
        row = (await s.execute(_MODELS, {"id": model_id})).one_or_none()
        if row is None:
            raise await _not_found(ctx, "model_registry", model_id)
        event = {"object_type": "model_registry", "object_id": str(model_id)}
        try:
            await s.execute(
                text(
                    "UPDATE core.model_registry SET is_enabled = :e, changed_by = :u, change_reason = :r WHERE id = :id"
                ),
                {"e": body.enabled, "u": ctx.user.id, "r": body.reason, "id": model_id},
            )
        except DBAPIError as exc:
            if _sqlstate(exc) != "55000":
                raise
            message = (
                "Not approved: it cannot be switched on"
                if row.approval_status != "APPROVED"
                else "Rollout gates not met: it cannot be switched on"
            )
            raise await ctx.deny(
                ctx.event(
                    "CONFIG_CHANGE",
                    "DENY",
                    reason="ROLLOUT_GATE",
                    **event,
                    after_meta={"model": f"{row.code}@{row.version}", "enabled": body.enabled},
                ),
                409,
                "rollout-gate",
                message,
            ) from None
        uow.audit(
            ctx.event(
                "CONFIG_CHANGE",
                before_meta={"enabled": row.is_enabled},
                after_meta={
                    "model": f"{row.code}@{row.version}",
                    "enabled": body.enabled,
                    "reason_length": len(body.reason),
                },
                **event,
            )
        )
        row = (await s.execute(_MODELS, {"id": model_id})).one()
        await uow.commit()
    return _model(row)


class AiFlagIn(SwitchIn):
    flag: Literal["ai_assistance", "ai_copilot", "ai_forecasting"]


class FlagsOut(BaseModel):
    ai_assistance: bool
    ai_copilot: bool
    ai_forecasting: bool


@router.post("/ai/settings")
async def set_ai_flag(ctx: ConfigCtx, body: AiFlagIn) -> FlagsOut:
    """The global AI kill switch (``ai_assistance``). Copilot and forecasting can be switched on only
    when their model is enabled (so only after its rollout gates are met)."""
    ctx.require_fresh_auth("ai.config")
    async with ctx.uow() as uow:
        s = uow.session
        if body.enabled and body.flag != "ai_assistance":
            code = "copilot-llm" if body.flag == "ai_copilot" else "forecast-review-priority"
            model_on = (
                await s.execute(
                    text("SELECT bool_or(is_enabled) FROM core.model_registry WHERE code = :c"),
                    {"c": code},
                )
            ).scalar_one()
            if not model_on:
                raise await ctx.deny(
                    ctx.event(
                        "CONFIG_CHANGE",
                        "DENY",
                        reason="ROLLOUT_GATE",
                        object_type="feature_flag",
                        object_id=body.flag,
                    ),
                    409,
                    "rollout-gate",
                    "Its model is not enabled: the rollout gates are not met",
                )
        before = await _flag(s, body.flag)
        await s.execute(
            text(
                "UPDATE core.feature_flag SET enabled = :e, changed_by = :u, changed_at = now() WHERE key = :k"
            ),
            {"e": body.enabled, "u": ctx.user.id, "k": body.flag},
        )
        uow.audit(
            ctx.event(
                "CONFIG_CHANGE",
                object_type="feature_flag",
                object_id=body.flag,
                before_meta={"enabled": before},
                after_meta={"enabled": body.enabled, "reason_length": len(body.reason)},
            )
        )
        flags = {k: await _flag(s, k) for k in ("ai_assistance", "ai_copilot", "ai_forecasting")}
        await uow.commit()
    return FlagsOut(**flags)


# --- template briefings ---------------------------------------------------------------------------------


class BriefingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    insight_ids: list[UUID] = Field(min_length=1, max_length=20)
    title: str = Field(default="Analyst briefing", min_length=5, max_length=120)

    _people = field_validator("title")(_no_people)


class BriefingSection(BaseModel):
    heading: str
    paragraphs: list[str]


class Citation(BaseModel):
    insight_id: UUID
    input_snapshot_id: UUID
    model: str


class BriefingOut(BaseModel):
    label: str
    title: str
    generated_at: datetime
    template: str
    sections: list[BriefingSection]
    citations: list[Citation]


@router.post("/ai/briefings")
async def create_briefing(ctx: ReviewCtx, body: BriefingIn) -> BriefingOut:
    """Fill the briefing template from insights an analyst has **validated** (each one cited). For
    reviewers (``ai.insight.review``: analysts; stewards for data-quality insights). A draft for the
    analyst to edit; nothing is stored, sent or exported."""
    async with ctx.uow() as uow:
        s = uow.session
        model = (
            await s.execute(
                text(
                    "SELECT code, version, is_enabled FROM core.model_registry WHERE code = 'briefing-template'"
                )
            )
        ).one_or_none()
        if not await _flag(s, "ai_assistance") or model is None or not model.is_enabled:
            raise await ctx.deny(
                ctx.event("AI_BRIEFING", "DENY", reason="AI_DISABLED", object_type="ai_briefing"),
                503,
                "feature-disabled",
                "Template briefings are switched off",
            )
        rows = []
        for insight_id in dict.fromkeys(body.insight_ids):
            row = await _load_insight(ctx, s, insight_id)
            if row.state != "VALIDATED" or not may_see(ctx, row, "ai.insight.review"):
                raise await ctx.deny(
                    ctx.event(
                        "AI_BRIEFING",
                        "DENY",
                        reason="NOT_VALIDATED",
                        object_type="ai_insight",
                        object_id=str(row.id),
                    ),
                    422,
                    "validation-error",
                    "Briefings use insights you may review that an analyst has validated",
                )
            rows.append(row)
        regions = sorted({r.region_code for r in rows})
        first = min(r.window_start for r in rows)
        last = max(r.window_end for r in rows)
        sections = [
            BriefingSection(
                heading="Summary",
                paragraphs=[
                    f"{len(rows)} validated insight{'s' if len(rows) != 1 else ''} for {', '.join(regions)}, "
                    f"covering {first:%d %b %Y} to {last:%d %b %Y}. Places and time windows only; no person is identified."
                ],
            ),
            BriefingSection(
                heading="Findings",
                paragraphs=[
                    f"[{i + 1}] {r.explanation} Confidence: {r.confidence_label.lower()}. Analyst note: {r.decision_note}"
                    for i, r in enumerate(rows)
                ],
            ),
            BriefingSection(
                heading="Limitations",
                paragraphs=sorted({lim for r in rows for lim in r.limitations}),
            ),
            BriefingSection(
                heading="Alternative explanations",
                paragraphs=sorted({alt for r in rows for alt in r.alternative_explanations}),
            ),
        ]
        uow.audit(
            ctx.event(
                "AI_BRIEFING",
                object_type="ai_briefing",
                after_meta={
                    "insights": [str(r.id) for r in rows],
                    "template": f"{model.code}@{model.version}",
                },
            )
        )
        await uow.commit()
    return BriefingOut(
        label=LABEL,
        title=body.title,
        generated_at=datetime.now(UTC),
        template=f"{model.code}@{model.version}",
        sections=sections,
        citations=[
            Citation(
                insight_id=r.id,
                input_snapshot_id=r.run_id,
                model=f"{r.model_code}@{r.model_version}",
            )
            for r in rows
        ],
    )


# --- copilot (disabled) ---------------------------------------------------------------------------------


class CopilotIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=3, max_length=1000)


@router.post("/ai/copilot/queries")
async def copilot_query(ctx: ReadCtx, body: CopilotIn) -> dict[str, Any]:
    """Refuses prohibited questions; otherwise ``503 feature-disabled`` (no approved language model).
    The question is never stored or logged (its length only)."""
    reason = copilot.refusal(body.question)
    meta = {"question_length": len(body.question)}
    if reason is not None:
        raise await ctx.deny(
            ctx.event(
                "AI_COPILOT_QUERY",
                "DENY",
                reason="PROHIBITED_QUESTION",
                object_type="ai_copilot",
                after_meta=meta,
            ),
            422,
            "prohibited-question",
            f"Questions about {reason} are not answered",
        )
    async with ctx.uow() as uow:
        enabled = await _flag(uow.session, "ai_copilot")
        await uow.commit()
    raise await ctx.deny(
        ctx.event(
            "AI_COPILOT_QUERY",
            "DENY",
            reason="FEATURE_DISABLED",
            object_type="ai_copilot",
            after_meta={**meta, "flag": enabled},
        ),
        503,
        "feature-disabled",
        "The analyst copilot is disabled: no approved language model is configured",
    )


_PENDING = text(
    """
SELECT count(*) FROM (
    SELECT 1 FROM core.ai_insight i JOIN core.model_registry m ON m.id = i.model_id
    WHERE i.state = 'PENDING' AND m.is_enabled
      AND ((i.insight_type IN ('DQ_ANOMALY', 'DUPLICATE', 'GEOCODE')
            AND (:dg OR i.region_id = ANY (:dr) OR i.station_id = ANY (:ds)))
        OR (i.insight_type IN ('EMERGING_PATTERN', 'CLUSTER')
            AND (:ag OR i.region_id = ANY (:ar) OR i.station_id = ANY (:as_))))
    LIMIT 1001) x
"""
)


async def pending_count(ctx: Ctx, s: AsyncSession) -> tuple[int, bool]:
    """Pending insights the caller may review (for the Review Queue badge), and whether over 1000.
    Scoped in SQL like the list, so other territories do not crowd out the caller's count."""
    if not await _flag(s, "ai_assistance"):
        return 0, False
    n = int((await s.execute(_PENDING, scope_params(ctx, "ai.insight.review"))).scalar_one())
    return min(n, 1000), n > 1000
