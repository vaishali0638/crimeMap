"""Review, action and outcome workflow (Phase 6, ADR-036).

State machines (the database enforces the same transitions, migrations 0010 and 0011):

- Pattern review (``pattern_review.create`` to write, ``pattern_review.decide`` to decide):
  DRAFT -> submit (author, with an assessment) -> SUBMITTED/PENDING -> decision by a *different*
  person with a rationale: APPROVED, REJECTED, or RETURNED (back to DRAFT for revision).
- Operational action (``action.propose`` / ``action.approve`` / ``action.complete``): proposed only
  from an APPROVED pattern review, within its cells; PROPOSED -> APPROVED | REJECTED by someone other
  than the proposer; APPROVED/NOT_STARTED -> COMPLETED with evidence, once started, by the assigned
  officer (or the station's SHO) — never the approver. Outcome review due 28 days after the end.
- Outcome review (``outcome.review``): for a COMPLETED action, by someone other than the person who
  completed it; metrics computed by the server from the review's own evidence query; DRAFT -> FINAL
  only with a baseline, at least one evidence reference and a conclusion; then immutable.

Every transition is audited (``WORKFLOW_TRANSITION``, with before/after state); the database writes
each item's history (``core.workflow_transition``) itself, so it cannot be forged by the API role.
Refusals are audited. Free text is never written to the audit log (lengths only). Actions are human
decisions: none is triggered automatically, and none targets a person.

What a caller sees of the evidence follows the evidence snapshot rule (ADR-035): the category label,
the snapshot link and the outcome numbers are shown only to those with the access that produced them.
"""

import json
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from crimemap_api.analytics.outcome import (
    change_summary,
    may_read_metrics,
    measure,
    parse_keys,
    public,
)
from crimemap_api.deps import AUTHENTICATED, Ctx, Requirement
from crimemap_api.http import ProblemError
from crimemap_api.policy.engine import PermissionScope, Target, authorize, permission_scope
from crimemap_api.routes.analytics import CELL_KEY, PERSON_TERMS, load_snapshot, may_read_snapshot
from crimemap_api.routes.map import IST
from crimemap_api.routes.search import looks_personal
from crimemap_api.territory import load_region

router = APIRouter(prefix="/api/v1", tags=["workflow"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]
CreateReviewCtx = Annotated[Ctx, Depends(Requirement("pattern_review.create"))]
DecideCtx = Annotated[Ctx, Depends(Requirement("pattern_review.decide"))]
ProposeCtx = Annotated[Ctx, Depends(Requirement("action.propose"))]
ApproveCtx = Annotated[Ctx, Depends(Requirement("action.approve"))]
CompleteCtx = Annotated[Ctx, Depends(Requirement("action.complete"))]
OutcomeCtx = Annotated[Ctx, Depends(Requirement("outcome.review"))]

OUTCOME_DUE_DAYS = 28
DECISION_DUE_DAYS = 7
QUEUE_LIMIT = 200
STATION = r"^[A-Z]{2,5}-S\d{2}$"
ACTION_TYPES = Literal[
    "PATROL_VISIBILITY",
    "COMMUNITY_ENGAGEMENT",
    "JOINT_INSPECTION",
    "ENVIRONMENTAL_REQUEST",
    "DATA_QUALITY_FIX",
    "OTHER",
]
# Audit object types (one name per table, for allowed and refused events alike).
AUDIT_TYPE = {
    "PATTERN_REVIEW": "pattern_review",
    "ACTION": "operational_action",
    "OUTCOME_REVIEW": "outcome_review",
}


def _today() -> date:
    return datetime.now(IST).date()


def _no_people(value: str | None) -> str | None:
    if value and (looks_personal(value) or PERSON_TERMS.search(value)):
        raise ValueError(
            "describe places, times and records only: no personal identifiers or references to individuals"
        )
    return value


def _deny(
    ctx: Ctx, reason: str, status: int, slug: str, object_type: str, object_id: Any, title: str
) -> Any:
    return ctx.deny(
        ctx.event(
            "WORKFLOW_TRANSITION",
            "DENY",
            reason=reason,
            object_type=object_type,
            object_id=str(object_id),
        ),
        status,
        slug,
        title,
    )


def _sod(ctx: Ctx, object_type: str, object_id: UUID, title: str) -> Any:
    return _deny(
        ctx, "SEPARATION_OF_DUTIES", 409, "separation-of-duties", object_type, object_id, title
    )


def _bad_state(ctx: Ctx, object_type: str, object_id: UUID, title: str) -> Any:
    return _deny(
        ctx, "INVALID_STATE", 409, "invalid-state-transition", object_type, object_id, title
    )


def _invalid(ctx: Ctx, object_type: str, object_id: Any, title: str) -> Any:
    return _deny(ctx, "INVALID_REQUEST", 422, "validation-error", object_type, object_id, title)


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


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = exc.orig
    return getattr(orig, "sqlstate", None) or getattr(
        getattr(orig, "__cause__", None), "sqlstate", None
    )


async def _write(
    ctx: Ctx, s: AsyncSession, stmt: Any, params: dict[str, Any], object_type: str, object_id: Any
) -> Any:
    """Run a state change. A database refusal (a concurrent change, or a rule the triggers enforce)
    becomes an audited 409 instead of a 500."""
    try:
        return await s.execute(stmt, params)
    except DBAPIError as exc:
        state = _sqlstate(exc)
        if state in {"23505", "55000", "42501", "23514", "23503"}:
            reason = "SEPARATION_OF_DUTIES" if state == "42501" else "INVALID_STATE"
            raise await _deny(
                ctx,
                reason,
                409,
                "separation-of-duties" if state == "42501" else "invalid-state-transition",
                object_type,
                object_id,
                "This change is not allowed in the item's current state; reload and try again",
            ) from None
        raise


def audit_transition(
    ctx: Ctx,
    uow: Any,
    *,
    object_type: str,
    object_id: UUID,
    from_state: str | None,
    to_state: str,
    rationale: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Audit event for one transition (the history row is written by the database trigger)."""
    uow.audit(
        ctx.event(
            "WORKFLOW_TRANSITION",
            object_type=AUDIT_TYPE[object_type],
            object_id=str(object_id),
            before_meta={"state": from_state},
            after_meta={
                "state": to_state,
                "rationale_length": len(rationale or ""),
                **(meta or {}),
            },
        )
    )


def _snapshot(row: Any) -> Any:
    """The snapshot columns joined into a row, in the shape ``may_read_snapshot`` expects."""
    if row.snap_section is None:  # not visible (row-level security) or missing
        return None
    return SimpleNamespace(
        created_by=row.snap_author,
        section=row.snap_section,
        region_ids=row.snap_region_ids,
        station_ids=row.snap_station_ids,
        may_mask=row.snap_may_mask,
    )


def evidence_visible(ctx: Ctx, row: Any) -> bool:
    """The category label and evidence link are shown only with the access that produced them."""
    snap = _snapshot(row)
    return snap is not None and may_read_snapshot(ctx, snap)


def _in_scope(scope: PermissionScope) -> dict[str, Any]:
    return {"g": scope.is_global, "r": list(scope.region_ids), "s": list(scope.station_ids)}


# --- pattern reviews ----------------------------------------------------------------------------------

_REVIEW = text(
    """
SELECT p.id, p.review_type, p.grid_cell_ids, p.category_label, p.analytical_category_id,
       p.period_start, p.period_end, p.baseline_start, p.baseline_end, p.record_count, p.baseline_count,
       p.quality_summary, p.evidence_snapshot_id, p.analyst_narrative, p.recommended_next_step, p.analyst_assessment,
       p.analyst_status, p.supervisor_status, p.decision_rationale, p.decided_by, p.decided_at, p.submitted_at,
       p.region_id, p.station_id, p.created_by, p.created_at, u.username AS author, d.username AS decider,
       r.code AS region_code, s.code AS station_code, es.section AS snap_section, es.created_by AS snap_author, es.region_ids AS snap_region_ids,
       es.station_ids AS snap_station_ids, es.may_mask AS snap_may_mask
FROM core.pattern_review p
JOIN core.organizational_unit r ON r.id = p.region_id
LEFT JOIN core.organizational_unit s ON s.id = p.station_id
LEFT JOIN core.evidence_snapshot es ON es.id = p.evidence_snapshot_id
LEFT JOIN core.app_user u ON u.id = p.created_by
LEFT JOIN core.app_user d ON d.id = p.decided_by
WHERE p.id = :id
"""
)


class ReviewDetail(BaseModel):
    id: UUID
    review_type: str
    cell_keys: list[str]
    category: str | None  # None: produced with access the caller does not have
    period_start: date
    period_end: date
    baseline_start: date
    baseline_end: date
    record_count: int
    baseline_count: int | None
    quality_summary: dict[str, Any]
    evidence_snapshot_id: UUID | None
    narrative: str
    recommended_next_step: str
    analyst_assessment: str | None
    analyst_status: str
    supervisor_status: str | None
    decision_rationale: str | None
    decided_by: str | None
    decided_at: datetime | None
    submitted_at: datetime | None
    region: str
    station: str | None
    author: str | None
    created_at: datetime
    created_by_me: bool
    may_edit: bool
    may_submit: bool
    may_decide: bool
    may_propose_action: bool


def _review_detail(ctx: Ctx, row: Any) -> ReviewDetail:
    target = Target(region_id=row.region_id, station_id=row.station_id)
    mine = row.created_by == ctx.user.id
    may_write = (
        mine
        and row.analyst_status == "DRAFT"
        and authorize(ctx.subject, "pattern_review.create", target=target).allowed
    )
    visible = evidence_visible(ctx, row)
    return ReviewDetail(
        id=row.id,
        review_type=row.review_type,
        cell_keys=list(row.grid_cell_ids),
        category=row.category_label if visible else None,
        period_start=row.period_start,
        period_end=row.period_end,
        baseline_start=row.baseline_start,
        baseline_end=row.baseline_end,
        record_count=row.record_count,
        baseline_count=row.baseline_count,
        quality_summary=row.quality_summary,
        evidence_snapshot_id=row.evidence_snapshot_id if visible else None,
        narrative=row.analyst_narrative,
        recommended_next_step=row.recommended_next_step,
        analyst_assessment=row.analyst_assessment,
        analyst_status=row.analyst_status,
        supervisor_status=row.supervisor_status,
        decision_rationale=row.decision_rationale,
        decided_by=row.decider,
        decided_at=row.decided_at,
        submitted_at=row.submitted_at,
        region=row.region_code,
        station=row.station_code,
        author=row.author,
        created_at=row.created_at,
        created_by_me=mine,
        may_edit=may_write,
        may_submit=may_write,
        may_decide=(not mine)
        and row.supervisor_status == "PENDING"
        and authorize(ctx.subject, "pattern_review.decide", target=target).allowed,
        may_propose_action=row.supervisor_status == "APPROVED"
        and authorize(ctx.subject, "action.propose", target=target).allowed,
    )


async def _load_review(ctx: Ctx, session: AsyncSession, review_id: UUID) -> Any:
    row = (await session.execute(_REVIEW, {"id": review_id})).one_or_none()
    target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
    if row is None or not any(
        authorize(ctx.subject, p, target=target).allowed
        for p in ("pattern_review.create", "pattern_review.decide")
    ):
        raise await _not_found(ctx, "pattern_review", review_id)
    return row


@router.get("/pattern-reviews/{review_id}")
async def get_pattern_review(ctx: AuthenticatedCtx, review_id: UUID) -> ReviewDetail:
    async with ctx.uow() as uow:
        row = await _load_review(ctx, uow.session, review_id)
        uow.audit(ctx.event("RECORD_VIEW", object_type="pattern_review", object_id=str(review_id)))
        await uow.commit()
    return _review_detail(ctx, row)


class ReviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    narrative: str | None = Field(default=None, min_length=20, max_length=4000)
    recommended_next_step: str | None = Field(default=None, min_length=5, max_length=1000)

    _people = field_validator("narrative", "recommended_next_step")(_no_people)


async def _author_draft(ctx: Ctx, s: AsyncSession, review_id: UUID, verb: str) -> Any:
    """Lock and load a review the caller may revise or submit: theirs, a draft, in their territory."""
    await s.execute(
        text("SELECT 1 FROM core.pattern_review WHERE id = :id FOR UPDATE"), {"id": review_id}
    )
    row = await _load_review(ctx, s, review_id)
    ctx.require(
        "pattern_review.create", target=Target(region_id=row.region_id, station_id=row.station_id)
    )
    if row.created_by != ctx.user.id:
        raise await _sod(
            ctx, "pattern_review", review_id, f"Only the author may {verb} a pattern review"
        )
    if row.analyst_status != "DRAFT":
        raise await _bad_state(
            ctx, "pattern_review", review_id, "Only a draft (or returned) review can be changed"
        )
    return row


@router.patch("/pattern-reviews/{review_id}")
async def edit_pattern_review(
    ctx: CreateReviewCtx, review_id: UUID, body: ReviewEdit
) -> ReviewDetail:
    async with ctx.uow() as uow:
        s = uow.session
        await _author_draft(ctx, s, review_id, "revise")
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.pattern_review SET analyst_narrative = coalesce(:n, analyst_narrative), "
                "recommended_next_step = coalesce(:x, recommended_next_step) WHERE id = :id"
            ),
            {"n": body.narrative, "x": body.recommended_next_step, "id": review_id},
            "pattern_review",
            review_id,
        )
        uow.audit(
            ctx.event(
                "DATA_CHANGE",
                object_type="pattern_review",
                object_id=str(review_id),
                after_meta={
                    "narrative_length": len(body.narrative or ""),
                    "next_step_length": len(body.recommended_next_step or ""),
                },
            )
        )
        row = (await s.execute(_REVIEW, {"id": review_id})).one()
        await uow.commit()
    return _review_detail(ctx, row)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    analyst_assessment: str = Field(min_length=10, max_length=4000)

    _people = field_validator("analyst_assessment")(_no_people)


@router.post("/pattern-reviews/{review_id}/submissions")
async def submit_pattern_review(
    ctx: CreateReviewCtx, review_id: UUID, body: SubmitIn
) -> ReviewDetail:
    async with ctx.uow() as uow:
        s = uow.session
        row = await _author_draft(ctx, s, review_id, "submit")
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.pattern_review SET analyst_status = 'SUBMITTED', supervisor_status = 'PENDING', "
                "analyst_assessment = :a WHERE id = :id"
            ),
            {"a": body.analyst_assessment, "id": review_id},
            "pattern_review",
            review_id,
        )
        audit_transition(
            ctx,
            uow,
            object_type="PATTERN_REVIEW",
            object_id=review_id,
            from_state=row.supervisor_status or "DRAFT",
            to_state="PENDING",
            rationale=None,
        )
        row = (await s.execute(_REVIEW, {"id": review_id})).one()
        await uow.commit()
    return _review_detail(ctx, row)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["APPROVE", "RETURN", "REJECT"]
    rationale: str = Field(min_length=10, max_length=4000)

    _people = field_validator("rationale")(_no_people)


@router.post("/pattern-reviews/{review_id}/decisions")
async def decide_pattern_review(ctx: DecideCtx, review_id: UUID, body: DecisionIn) -> ReviewDetail:
    async with ctx.uow() as uow:
        s = uow.session
        await s.execute(
            text("SELECT 1 FROM core.pattern_review WHERE id = :id FOR UPDATE"), {"id": review_id}
        )
        row = await _load_review(ctx, s, review_id)
        ctx.require(
            "pattern_review.decide",
            target=Target(region_id=row.region_id, station_id=row.station_id),
        )
        if row.created_by == ctx.user.id:
            raise await _sod(
                ctx,
                "pattern_review",
                review_id,
                "A pattern review is decided by someone other than its author",
            )
        if row.supervisor_status != "PENDING":
            raise await _bad_state(
                ctx, "pattern_review", review_id, "This review is not awaiting a decision"
            )
        new = {"APPROVE": "APPROVED", "RETURN": "RETURNED", "REJECT": "REJECTED"}[body.decision]
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.pattern_review SET supervisor_status = :st, analyst_status = :an, decision_rationale = :r, "
                "decided_by = :u WHERE id = :id"
            ),
            {
                "st": new,
                "an": "DRAFT" if new == "RETURNED" else "SUBMITTED",
                "r": body.rationale,
                "u": ctx.user.id,
                "id": review_id,
            },
            "pattern_review",
            review_id,
        )
        audit_transition(
            ctx,
            uow,
            object_type="PATTERN_REVIEW",
            object_id=review_id,
            from_state="PENDING",
            to_state=new,
            rationale=body.rationale,
        )
        row = (await s.execute(_REVIEW, {"id": review_id})).one()
        await uow.commit()
    return _review_detail(ctx, row)


# --- operational actions ------------------------------------------------------------------------------

_ACTION = text(
    """
SELECT a.*, r.code AS region_code, s.code AS station_code, s.name AS station_name, pu.username AS proposer,
       au.username AS approver, cu.username AS completer, asg.username AS assignee, p.category_label,
       p.evidence_snapshot_id, o.id AS outcome_id, o.status AS outcome_status, es.section AS snap_section, es.created_by AS snap_author, es.region_ids AS snap_region_ids,
       es.station_ids AS snap_station_ids, es.may_mask AS snap_may_mask
FROM core.operational_action a
LEFT JOIN core.outcome_review o ON o.action_id = a.id
JOIN core.organizational_unit r ON r.id = a.region_id
JOIN core.organizational_unit s ON s.id = a.station_id
LEFT JOIN core.pattern_review p ON p.id = a.pattern_review_id  -- may be hidden by row-level security
LEFT JOIN core.evidence_snapshot es ON es.id = p.evidence_snapshot_id
LEFT JOIN core.app_user pu ON pu.id = a.proposed_by
LEFT JOIN core.app_user au ON au.id = a.approved_by
LEFT JOIN core.app_user cu ON cu.id = a.completed_by
LEFT JOIN core.app_user asg ON asg.id = a.assignee_id
WHERE a.id = :id
"""
)


class ActionOut(BaseModel):
    id: UUID
    pattern_review_id: UUID
    pattern_review_visible: bool
    action_type: str
    description: str
    cell_keys: list[str]
    region: str
    station: str
    station_name: str
    assignee: str | None
    start_at: date
    end_at: date
    approval_status: str
    approval_rationale: str | None
    proposer: str | None
    approver: str | None
    approved_at: datetime | None
    completion_status: str
    completion_evidence: str | None
    completion_reference: str | None
    completer: str | None
    completed_at: datetime | None
    outcome_review_due_at: date
    category: str | None
    evidence_snapshot_id: UUID | None
    may_approve: bool
    may_complete: bool
    may_review_outcome: bool
    outcome_id: UUID | None
    outcome_status: str | None


def _action_target(row: Any) -> Target:
    return Target(region_id=row.region_id, station_id=row.station_id, assignee_id=row.assignee_id)


def _action_out(ctx: Ctx, row: Any) -> ActionOut:
    target = _action_target(row)
    visible = evidence_visible(ctx, row)
    me = ctx.user.id
    review_visible = row.category_label is not None and any(
        authorize(
            ctx.subject, p, target=Target(region_id=row.region_id, station_id=row.station_id)
        ).allowed
        for p in ("pattern_review.create", "pattern_review.decide")
    )
    return ActionOut(
        id=row.id,
        pattern_review_id=row.pattern_review_id,
        pattern_review_visible=review_visible,
        action_type=row.action_type,
        description=row.description,
        cell_keys=list(row.grid_cell_ids),
        region=row.region_code,
        station=row.station_code,
        station_name=row.station_name,
        assignee=row.assignee,
        start_at=row.start_at,
        end_at=row.end_at,
        approval_status=row.approval_status,
        approval_rationale=row.approval_rationale,
        proposer=row.proposer,
        approver=row.approver,
        approved_at=row.approved_at,
        completion_status=row.completion_status,
        completion_evidence=row.completion_evidence,
        completion_reference=row.completion_reference,
        completer=row.completer,
        completed_at=row.completed_at,
        outcome_review_due_at=row.outcome_review_due_at,
        category=row.category_label if visible else None,
        evidence_snapshot_id=row.evidence_snapshot_id if visible else None,
        may_approve=row.approval_status == "PROPOSED"
        and row.proposed_by != me
        and authorize(ctx.subject, "action.approve", target=target).allowed,
        may_complete=row.approval_status == "APPROVED"
        and row.completion_status == "NOT_STARTED"
        and row.approved_by != me
        and row.start_at <= _today()
        and authorize(ctx.subject, "action.complete", target=target).allowed,
        may_review_outcome=row.completion_status == "COMPLETED"
        and row.completed_by != me
        and authorize(ctx.subject, "outcome.review", target=target).allowed,
        outcome_id=row.outcome_id,
        outcome_status=row.outcome_status,
    )


ACTION_PERMISSIONS = ("action.propose", "action.approve", "action.complete", "outcome.review")


async def _load_action(ctx: Ctx, session: AsyncSession, action_id: UUID) -> Any:
    row = (await session.execute(_ACTION, {"id": action_id})).one_or_none()
    if row is None or not any(
        authorize(ctx.subject, p, target=_action_target(row)).allowed for p in ACTION_PERMISSIONS
    ):
        raise await _not_found(ctx, "operational_action", action_id)
    return row


async def _lock_action(ctx: Ctx, s: AsyncSession, action_id: UUID) -> Any:
    await s.execute(
        text("SELECT 1 FROM core.operational_action WHERE id = :id FOR UPDATE"), {"id": action_id}
    )
    return await _load_action(ctx, s, action_id)


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pattern_review_id: UUID
    action_type: ACTION_TYPES
    description: str = Field(min_length=20, max_length=2000)
    cell_keys: list[Annotated[str, Field(pattern=CELL_KEY)]] = Field(min_length=1, max_length=200)
    station: str = Field(pattern=STATION)
    assignee_id: UUID | None = None
    start_at: date
    end_at: date

    _people = field_validator("description")(_no_people)

    @model_validator(mode="after")
    def _dates(self) -> "ActionIn":
        if self.end_at < self.start_at or (self.end_at - self.start_at).days > 366:
            raise ValueError("the action runs from start_at to end_at, at most a year")
        if len(set(self.cell_keys)) != len(self.cell_keys):
            raise ValueError("each cell may be selected once")
        return self


_ASSIGNEES = text(
    """
SELECT u.id, u.username, u.name FROM core.app_user u
JOIN core.user_role_grant g ON g.user_id = u.id
WHERE u.status = 'ACTIVE' AND g.revoked_at IS NULL AND g.valid_from <= now()
  AND (g.valid_to IS NULL OR g.valid_to > now())
  AND g.role_code IN ('STATION_OFFICER', 'SHO') AND :station = ANY (g.scope_station_ids)
GROUP BY u.id, u.username, u.name ORDER BY u.username
"""
)


class Assignee(BaseModel):
    id: UUID
    name: str


class AssigneeList(BaseModel):
    data: list[Assignee]


@router.get("/actions/assignees")
async def list_assignees(
    ctx: AuthenticatedCtx, station: Annotated[str, Query(pattern=STATION)]
) -> AssigneeList:
    """Officers who can be assigned an action at ``station`` (for those who propose or approve there)."""
    async with ctx.uow() as uow:
        units = await load_region(uow.session, station.split("-")[0])
        st = units.stations.get(station) if units else None
        if (
            units is None
            or st is None
            or not any(
                authorize(
                    ctx.subject, p, target=Target(region_id=units.region.id, station_id=st.id)
                ).allowed
                for p in ("action.propose", "action.approve")
            )
        ):
            raise await _not_found(ctx, "station", station)
        rows = (await uow.session.execute(_ASSIGNEES, {"station": st.id})).all()
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="station_assignees",
                object_id=station,
                after_meta={"count": len(rows)},
            )
        )
        await uow.commit()
    return AssigneeList(
        data=[Assignee(id=r.id, name=r.name or r.username or str(r.id)) for r in rows]
    )


async def _valid_assignee(session: AsyncSession, station_id: UUID, assignee: UUID | None) -> bool:
    if assignee is None:
        return True
    return any(
        r.id == assignee for r in (await session.execute(_ASSIGNEES, {"station": station_id})).all()
    )


@router.post("/actions", status_code=201)
async def propose_action(ctx: ProposeCtx, body: ActionIn) -> JSONResponse:
    """Retroactive actions (dates in the past) are allowed: a station may record an action already
    taken. Completion still needs the action to have started (ADR-036)."""
    async with ctx.uow() as uow:
        s = uow.session
        review = await _load_review(ctx, s, body.pattern_review_id)
        if review.supervisor_status != "APPROVED":
            raise await _bad_state(
                ctx,
                "pattern_review",
                body.pattern_review_id,
                "Actions follow an approved pattern review",
            )
        if set(body.cell_keys) - set(review.grid_cell_ids):
            raise await _invalid(
                ctx,
                "pattern_review",
                body.pattern_review_id,
                "The action's cells must be within the pattern review's cells",
            )
        units = await load_region(s, review.region_code)
        station = units.stations.get(body.station) if units else None
        if units is None or station is None:
            raise await _invalid(
                ctx,
                "pattern_review",
                body.pattern_review_id,
                "The assigned station must be in the review's region",
            )
        ctx.require(
            "action.propose", target=Target(region_id=units.region.id, station_id=station.id)
        )
        if not await _valid_assignee(s, station.id, body.assignee_id):
            raise await _invalid(
                ctx,
                "pattern_review",
                body.pattern_review_id,
                "The assignee must be an officer of the assigned station",
            )
        _region, size, cells = parse_keys(body.cell_keys)
        action_id = (
            await _write(
                ctx,
                s,
                text(
                    """
INSERT INTO core.operational_action (pattern_review_id, action_type, description, grid_cell_ids, territory_geometry,
    region_id, station_id, assignee_id, start_at, end_at, proposed_by, outcome_review_due_at)
SELECT :review, :type, :description, CAST(:keys AS text[]),
       (SELECT ST_Multi(ST_Union(core.hex_polygon(t.q, t.r, :size, :crs)))
          FROM unnest(CAST(:qs AS int[]), CAST(:rs AS int[])) AS t(q, r)),
       :region, :station, :assignee, :start_at, :end_at, :user, :due
RETURNING id
"""
                ),
                {
                    "review": body.pattern_review_id,
                    "type": body.action_type,
                    "description": body.description,
                    "keys": sorted(body.cell_keys),
                    "size": float(size),
                    "crs": units.crs,
                    "qs": [c[0] for c in cells],
                    "rs": [c[1] for c in cells],
                    "region": units.region.id,
                    "station": station.id,
                    "assignee": body.assignee_id,
                    "start_at": body.start_at,
                    "end_at": body.end_at,
                    "user": ctx.user.id,
                    "due": body.end_at + timedelta(days=OUTCOME_DUE_DAYS),
                },
                "pattern_review",
                body.pattern_review_id,
            )
        ).scalar_one()
        audit_transition(
            ctx,
            uow,
            object_type="ACTION",
            object_id=action_id,
            from_state=None,
            to_state="PROPOSED",
            rationale=None,
            meta={
                "action_type": body.action_type,
                "cells": len(body.cell_keys),
                "pattern_review_id": str(body.pattern_review_id),
            },
        )
        row = (await s.execute(_ACTION, {"id": action_id})).one()
        await uow.commit()
    return JSONResponse(status_code=201, content=_action_out(ctx, row).model_dump(mode="json"))


class ActionDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=10, max_length=2000)
    assignee_id: UUID | None = None

    _people = field_validator("rationale")(_no_people)


@router.post("/actions/{action_id}/approvals")
async def approve_action(ctx: ApproveCtx, action_id: UUID, body: ActionDecisionIn) -> ActionOut:
    async with ctx.uow() as uow:
        s = uow.session
        row = await _lock_action(ctx, s, action_id)
        ctx.require(
            "action.approve", target=Target(region_id=row.region_id, station_id=row.station_id)
        )
        if row.proposed_by == ctx.user.id:
            raise await _sod(
                ctx,
                "operational_action",
                action_id,
                "An action is approved by someone other than its proposer",
            )
        if row.approval_status != "PROPOSED":
            raise await _bad_state(
                ctx, "operational_action", action_id, "This action has already been decided"
            )
        assignee = body.assignee_id or row.assignee_id
        if not await _valid_assignee(s, row.station_id, assignee):
            raise await _invalid(
                ctx,
                "operational_action",
                action_id,
                "The assignee must be an officer of the assigned station",
            )
        new = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.operational_action SET approval_status = :st, approval_rationale = :r, approved_by = :u, "
                "assignee_id = :a WHERE id = :id"
            ),
            {"st": new, "r": body.rationale, "u": ctx.user.id, "a": assignee, "id": action_id},
            "operational_action",
            action_id,
        )
        audit_transition(
            ctx,
            uow,
            object_type="ACTION",
            object_id=action_id,
            from_state="PROPOSED",
            to_state=new,
            rationale=body.rationale,
        )
        row = (await s.execute(_ACTION, {"id": action_id})).one()
        await uow.commit()
    return _action_out(ctx, row)


class CompletionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence: str = Field(min_length=10, max_length=2000)
    reference: str | None = Field(default=None, max_length=500)

    _people = field_validator("evidence", "reference")(_no_people)


@router.post("/actions/{action_id}/completions")
async def complete_action(ctx: CompleteCtx, action_id: UUID, body: CompletionIn) -> ActionOut:
    async with ctx.uow() as uow:
        s = uow.session
        row = await _lock_action(ctx, s, action_id)
        # Station officers only for actions assigned to them; SHOs for their station (role matrix).
        ctx.require("action.complete", target=_action_target(row))
        if row.approval_status != "APPROVED" or row.completion_status != "NOT_STARTED":
            raise await _bad_state(
                ctx,
                "operational_action",
                action_id,
                "Only an approved, not yet completed action can be completed",
            )
        if row.approved_by == ctx.user.id:
            raise await _sod(
                ctx,
                "operational_action",
                action_id,
                "Completion is recorded by someone other than the approver",
            )
        if row.start_at > _today():
            raise await _bad_state(
                ctx,
                "operational_action",
                action_id,
                "An action cannot be completed before it starts",
            )
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.operational_action SET completion_status = 'COMPLETED', completion_evidence = :e, "
                "completion_reference = :ref, completed_by = :u WHERE id = :id"
            ),
            {"e": body.evidence, "ref": body.reference, "u": ctx.user.id, "id": action_id},
            "operational_action",
            action_id,
        )
        audit_transition(
            ctx,
            uow,
            object_type="ACTION",
            object_id=action_id,
            from_state="APPROVED",
            to_state="COMPLETED",
            rationale=None,
            meta={
                "evidence_length": len(body.evidence),
                "has_reference": body.reference is not None,
            },
        )
        row = (await s.execute(_ACTION, {"id": action_id})).one()
        await uow.commit()
    return _action_out(ctx, row)


@router.get("/actions/{action_id}")
async def get_action(ctx: AuthenticatedCtx, action_id: UUID) -> ActionOut:
    async with ctx.uow() as uow:
        row = await _load_action(ctx, uow.session, action_id)
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="operational_action", object_id=str(action_id))
        )
        await uow.commit()
    return _action_out(ctx, row)


# --- outcome reviews ----------------------------------------------------------------------------------

_OUTCOME = text(
    """
SELECT o.*, u.username AS reviewer, a.grid_cell_ids AS action_cells, a.station_id AS action_station
FROM core.outcome_review o
JOIN core.operational_action a ON a.id = o.action_id
LEFT JOIN core.app_user u ON u.id = o.created_by
WHERE o.id = :id
"""
)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["EVIDENCE_SNAPSHOT", "DOCUMENT"]
    value: str = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def _value(self) -> "EvidenceRef":
        # A snapshot reference is an id (its digits can look like a phone number, so the
        # free-text check would refuse ~5% of valid ids); a document reference is free text.
        if self.kind == "EVIDENCE_SNAPSHOT":
            try:
                self.value = str(UUID(self.value))
            except ValueError:
                raise ValueError("an evidence snapshot reference must be a snapshot id") from None
        else:
            _no_people(self.value)
        return self


class OutcomeOut(BaseModel):
    id: UUID
    action_id: UUID
    selected_measures: list[str]
    baseline_start: date
    baseline_end: date
    after_start: date
    after_end: date
    comparison_kind: str
    comparison_keys: list[str]
    metrics_visible: bool  # False: computed with access the caller does not have
    before_metrics: dict[str, Any]
    after_metrics: dict[str, Any]
    summary: list[str]
    displacement_diffusion_notes: str | None
    evidence_references: list[dict[str, Any]]
    reviewer_conclusion: str | None
    status: str
    finalised_at: datetime | None
    reviewer: str | None
    created_at: datetime
    updated_at: datetime
    may_edit: bool


def _outcome_out(ctx: Ctx, row: Any) -> OutcomeOut:
    visible = may_read_metrics(ctx, row.before_metrics, row.created_by, row.region_id) and (
        may_read_metrics(ctx, row.after_metrics, row.created_by, row.region_id)
    )
    return OutcomeOut(
        id=row.id,
        action_id=row.action_id,
        selected_measures=list(row.selected_measures),
        baseline_start=row.baseline_start,
        baseline_end=row.baseline_end,
        after_start=row.after_start,
        after_end=row.after_end,
        comparison_kind=row.comparison_kind,
        comparison_keys=list(row.comparison_keys),
        metrics_visible=visible,
        before_metrics=public(row.before_metrics) if visible else {},
        after_metrics=public(row.after_metrics) if visible else {},
        summary=change_summary(row.before_metrics, row.after_metrics) if visible else [],
        displacement_diffusion_notes=row.displacement_diffusion_notes,
        evidence_references=list(row.evidence_references),
        reviewer_conclusion=row.reviewer_conclusion,
        status=row.status,
        finalised_at=row.finalised_at,
        reviewer=row.reviewer,
        created_at=row.created_at,
        updated_at=row.updated_at,
        may_edit=row.status == "DRAFT" and row.created_by == ctx.user.id,
    )


async def _load_outcome(ctx: Ctx, session: AsyncSession, outcome_id: UUID) -> Any:
    row = (await session.execute(_OUTCOME, {"id": outcome_id})).one_or_none()
    target = Target(region_id=row.region_id, station_id=row.station_id) if row else None
    if row is None or not any(
        authorize(ctx.subject, p, target=target).allowed
        for p in ("outcome.review", "action.approve")
    ):
        raise await _not_found(ctx, "outcome_review", outcome_id)
    return row


class OutcomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    baseline_start: date
    baseline_end: date
    after_start: date
    after_end: date
    comparison_kind: Literal["CELLS", "STATION", "NONE"] = "NONE"
    comparison_keys: list[Annotated[str, Field(max_length=40)]] = Field(
        default_factory=list, max_length=200
    )

    @model_validator(mode="after")
    def _periods(self) -> "OutcomeIn":
        if not (self.baseline_start <= self.baseline_end < self.after_start <= self.after_end):
            raise ValueError("the baseline (before) period must end before the after period starts")
        if (self.baseline_end - self.baseline_start).days > 366 or (
            self.after_end - self.after_start
        ).days > 366:
            raise ValueError("each period may be at most a year")
        keys = self.comparison_keys
        if self.comparison_kind == "NONE" and keys:
            raise ValueError("give no comparison keys when there is no comparison area")
        if self.comparison_kind != "NONE" and not keys:
            raise ValueError("give the comparison area")
        if self.comparison_kind == "CELLS" and not all(re.match(CELL_KEY, k) for k in keys):
            raise ValueError("comparison cells must be cell keys")
        if self.comparison_kind == "STATION" and not (
            len(keys) == 1 and re.match(STATION, keys[0])
        ):
            raise ValueError("give one comparison station")
        if len(set(keys)) != len(keys):
            raise ValueError("each comparison key may be given once")
        return self


@router.post("/outcomes", status_code=201)
async def create_outcome(ctx: OutcomeCtx, body: OutcomeIn) -> JSONResponse:
    async with ctx.uow() as uow:
        s = uow.session
        # Lock the action: concurrent creations wait, then see the first review (409, not 500).
        action = await _lock_action(ctx, s, body.action_id)
        ctx.require(
            "outcome.review",
            target=Target(region_id=action.region_id, station_id=action.station_id),
        )
        if action.completion_status != "COMPLETED":
            raise await _bad_state(
                ctx,
                "operational_action",
                body.action_id,
                "Outcome reviews follow a completed action",
            )
        if action.completed_by == ctx.user.id:
            raise await _sod(
                ctx,
                "operational_action",
                body.action_id,
                "The outcome is reviewed by someone other than the person who completed the action",
            )
        if action.outcome_id is not None:
            raise await _bad_state(
                ctx,
                "operational_action",
                body.action_id,
                "This action already has an outcome review",
            )
        problems = []
        if body.after_start < action.start_at:
            problems.append("the after period starts when the action starts or later")
        if body.baseline_end >= action.start_at:
            problems.append("the baseline ends before the action starts")
        if body.after_end > _today():
            problems.append("the after period ends today or earlier (no partial counts)")
        region_code = action.region_code
        if body.comparison_kind == "CELLS":
            if any(not k.startswith(f"{region_code}:") for k in body.comparison_keys):
                problems.append("comparison cells must be in the action's region")
            if set(body.comparison_keys) & set(action.grid_cell_ids):
                problems.append("comparison cells must be outside the action's cells")
        if body.comparison_kind == "STATION":
            if not body.comparison_keys[0].startswith(f"{region_code}-"):
                problems.append("the comparison station must be in the action's region")
            if body.comparison_keys[0] == action.station_code:
                problems.append("the comparison station must differ from the action's station")
        if problems:
            raise await _invalid(
                ctx, "operational_action", body.action_id, "; ".join(problems).capitalize()
            )
        # The review's own evidence query: readable by this reviewer, otherwise they cannot measure it.
        snapshot = (
            await s.execute(
                text(
                    "SELECT es.params FROM core.pattern_review p JOIN core.evidence_snapshot es "
                    "ON es.id = p.evidence_snapshot_id WHERE p.id = :p"
                ),
                {"p": action.pattern_review_id},
            )
        ).one_or_none()
        if snapshot is None or not evidence_visible(ctx, action):
            raise await _deny(
                ctx,
                "EVIDENCE_NOT_VISIBLE",
                403,
                "forbidden",
                "operational_action",
                body.action_id,
                "The pattern review's evidence is outside your access, so you cannot measure this outcome",
            )
        periods = ((body.baseline_start, body.baseline_end), (body.after_start, body.after_end))
        try:
            before, after = [
                await measure(
                    ctx,
                    s,
                    params=dict(snapshot.params),
                    action_keys=list(action.grid_cell_ids),
                    start=start,
                    end=end,
                    comparison_kind=body.comparison_kind,
                    comparison_keys=body.comparison_keys,
                )
                for start, end in periods
            ]
        except ValueError as e:
            raise ProblemError(422, "validation-error", str(e)) from None
        outcome_id = (
            await _write(
                ctx,
                s,
                text(
                    """
INSERT INTO core.outcome_review (action_id, selected_measures, baseline_start, baseline_end, after_start, after_end,
    comparison_kind, comparison_keys, before_metrics, after_metrics, region_id, station_id, created_by)
VALUES (:a, :m, :bs, :be, :as_, :ae, :ck, CAST(:keys AS text[]), CAST(:before AS jsonb), CAST(:after AS jsonb), :region, :station, :u)
RETURNING id
"""
                ),
                {
                    "a": body.action_id,
                    "m": ["action_area", "surrounding_ring", "comparison_area"],
                    "bs": body.baseline_start,
                    "be": body.baseline_end,
                    "as_": body.after_start,
                    "ae": body.after_end,
                    "ck": body.comparison_kind,
                    "keys": body.comparison_keys,
                    "before": json.dumps(before, default=str),
                    "after": json.dumps(after, default=str),
                    "region": action.region_id,
                    "station": action.station_id,
                    "u": ctx.user.id,
                },
                "operational_action",
                body.action_id,
            )
        ).scalar_one()
        audit_transition(
            ctx,
            uow,
            object_type="OUTCOME_REVIEW",
            object_id=outcome_id,
            from_state=None,
            to_state="DRAFT",
            rationale=None,
            meta={"action_id": str(body.action_id)},
        )
        row = (await s.execute(_OUTCOME, {"id": outcome_id})).one()
        await uow.commit()
    return JSONResponse(status_code=201, content=_outcome_out(ctx, row).model_dump(mode="json"))


class OutcomeEdit(BaseModel):
    """``None`` keeps a field; an empty string clears it."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    displacement_diffusion_notes: str | None = Field(default=None, max_length=4000)
    reviewer_conclusion: str | None = Field(default=None, max_length=4000)
    evidence_references: list[EvidenceRef] | None = Field(default=None, max_length=20)

    _people = field_validator("displacement_diffusion_notes", "reviewer_conclusion")(_no_people)

    @model_validator(mode="after")
    def _distinct(self) -> "OutcomeEdit":
        refs = self.evidence_references or []
        if len({(r.kind, r.value) for r in refs}) != len(refs):
            raise ValueError("each evidence reference may be given once")
        return self


async def _check_refs(
    ctx: Ctx, session: AsyncSession, refs: list[EvidenceRef], region_id: UUID
) -> None:
    for ref in refs:
        if ref.kind == "EVIDENCE_SNAPSHOT":
            try:
                snapshot_id = UUID(ref.value)
            except ValueError:
                raise ProblemError(
                    422, "validation-error", "An evidence snapshot reference must be a snapshot id"
                ) from None
            snap = await load_snapshot(
                ctx, session, snapshot_id
            )  # 404 if not visible to the reviewer
            if region_id not in snap.region_ids:
                raise ProblemError(
                    422, "validation-error", "An evidence snapshot must cover the action's region"
                )


@router.patch("/outcomes/{outcome_id}")
async def edit_outcome(ctx: OutcomeCtx, outcome_id: UUID, body: OutcomeEdit) -> OutcomeOut:
    async with ctx.uow() as uow:
        s = uow.session
        await s.execute(
            text("SELECT 1 FROM core.outcome_review WHERE id = :id FOR UPDATE"), {"id": outcome_id}
        )
        row = await _load_outcome(ctx, s, outcome_id)
        if row.created_by != ctx.user.id:
            raise await _sod(
                ctx, "outcome_review", outcome_id, "Only the reviewer edits an outcome review"
            )
        if row.status != "DRAFT":
            raise await _bad_state(
                ctx, "outcome_review", outcome_id, "A final outcome review cannot change"
            )
        if body.evidence_references is not None:
            await _check_refs(ctx, s, body.evidence_references, row.region_id)
        await _write(
            ctx,
            s,
            text(
                "UPDATE core.outcome_review SET "
                "displacement_diffusion_notes = CASE WHEN CAST(:n AS text) IS NULL THEN displacement_diffusion_notes "
                "  ELSE NULLIF(CAST(:n AS text), '') END, "
                "reviewer_conclusion = CASE WHEN CAST(:c AS text) IS NULL THEN reviewer_conclusion "
                "  ELSE NULLIF(CAST(:c AS text), '') END, "
                "evidence_references = coalesce(CAST(:e AS jsonb), evidence_references) WHERE id = :id"
            ),
            {
                "n": body.displacement_diffusion_notes,
                "c": body.reviewer_conclusion,
                "e": json.dumps([r.model_dump() for r in body.evidence_references])
                if body.evidence_references is not None
                else None,
                "id": outcome_id,
            },
            "outcome_review",
            outcome_id,
        )
        uow.audit(
            ctx.event(
                "DATA_CHANGE",
                object_type="outcome_review",
                object_id=str(outcome_id),
                after_meta={
                    "evidence_references": len(body.evidence_references or []),
                    "conclusion_length": len(body.reviewer_conclusion or ""),
                    "notes_length": len(body.displacement_diffusion_notes or ""),
                },
            )
        )
        row = (await s.execute(_OUTCOME, {"id": outcome_id})).one()
        await uow.commit()
    return _outcome_out(ctx, row)


@router.post("/outcomes/{outcome_id}/finalisations")
async def finalise_outcome(ctx: OutcomeCtx, outcome_id: UUID) -> OutcomeOut:
    async with ctx.uow() as uow:
        s = uow.session
        await s.execute(
            text("SELECT 1 FROM core.outcome_review WHERE id = :id FOR UPDATE"), {"id": outcome_id}
        )
        row = await _load_outcome(ctx, s, outcome_id)
        if row.created_by != ctx.user.id:
            raise await _sod(
                ctx, "outcome_review", outcome_id, "Only the reviewer finalises an outcome review"
            )
        if row.status != "DRAFT":
            raise await _bad_state(
                ctx, "outcome_review", outcome_id, "This outcome review is already final"
            )
        missing = []
        if not row.baseline_start or not row.baseline_end or not row.before_metrics:
            missing.append("baseline")
        if not row.evidence_references:
            missing.append("evidence references")
        if len(row.reviewer_conclusion or "") < 20:
            missing.append("a conclusion (at least 20 characters)")
        if missing:
            raise await ctx.deny(
                ctx.event(
                    "WORKFLOW_TRANSITION",
                    "DENY",
                    reason="INCOMPLETE",
                    object_type="outcome_review",
                    object_id=str(outcome_id),
                    after_meta={"missing": missing},
                ),
                422,
                "validation-error",
                "Cannot finalise without " + ", ".join(missing),
            )
        await _write(
            ctx,
            s,
            text("UPDATE core.outcome_review SET status = 'FINAL' WHERE id = :id"),
            {"id": outcome_id},
            "outcome_review",
            outcome_id,
        )
        audit_transition(
            ctx,
            uow,
            object_type="OUTCOME_REVIEW",
            object_id=outcome_id,
            from_state="DRAFT",
            to_state="FINAL",
            rationale=None,
            meta={"evidence_references": len(row.evidence_references)},
        )
        row = (await s.execute(_OUTCOME, {"id": outcome_id})).one()
        await uow.commit()
    return _outcome_out(ctx, row)


@router.get("/outcomes/{outcome_id}")
async def get_outcome(ctx: AuthenticatedCtx, outcome_id: UUID) -> OutcomeOut:
    async with ctx.uow() as uow:
        row = await _load_outcome(ctx, uow.session, outcome_id)
        uow.audit(ctx.event("RECORD_VIEW", object_type="outcome_review", object_id=str(outcome_id)))
        await uow.commit()
    return _outcome_out(ctx, row)


# --- queue and history ---------------------------------------------------------------------------------


class QueueItem(BaseModel):
    type: Literal["PATTERN_REVIEW", "ACTION", "OUTCOME_DUE"]
    id: UUID
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    opened_at: datetime  # created or submitted
    region: str
    station: str | None
    cells: int
    category: str | None  # None: not shown (see evidence_visible)
    owner: str | None  # author (reviews), assigned officer (actions), reviewer (outcomes)
    mine: bool  # the caller owns it
    due: date | None
    overdue: bool
    status: str
    evidence_snapshot_id: UUID | None
    next_step: Literal[
        "DECIDE", "SUBMIT", "RESUBMIT", "APPROVE", "COMPLETE", "START_OUTCOME", "CONTINUE_OUTCOME"
    ]


class QueueOut(BaseModel):
    queue: str
    data: list[QueueItem]
    more: bool  # more items than shown (the oldest are shown first)


# Each query selects only items the caller could act on (state and permission territory); the
# per-item policy check below is then exact. Limited to QUEUE_LIMIT + 1 to report `more`.
_QUEUE_REVIEWS = text(
    """
SELECT p.id, p.created_at, p.submitted_at, p.analyst_status, p.supervisor_status, p.record_count, p.category_label,
       p.evidence_snapshot_id, p.created_by, p.region_id, p.station_id, u.username AS owner, r.code AS region_code,
       s.code AS station_code, cardinality(p.grid_cell_ids) AS cells, es.section AS snap_section, es.created_by AS snap_author, es.region_ids AS snap_region_ids,
       es.station_ids AS snap_station_ids, es.may_mask AS snap_may_mask
FROM core.pattern_review p
JOIN core.organizational_unit r ON r.id = p.region_id
LEFT JOIN core.organizational_unit s ON s.id = p.station_id
LEFT JOIN core.evidence_snapshot es ON es.id = p.evidence_snapshot_id
LEFT JOIN core.app_user u ON u.id = p.created_by
WHERE ((p.supervisor_status = 'PENDING' AND p.created_by <> :me
        AND (:g OR p.region_id = ANY (:r) OR p.station_id = ANY (:s)))
    OR (p.created_by = :me AND p.analyst_status = 'DRAFT'))
  AND (CAST(:region AS text) IS NULL OR r.code = :region)
  AND (NOT :mine OR p.created_by = :me)
  AND (NOT :overdue OR (p.supervisor_status = 'PENDING'
       AND (p.submitted_at AT TIME ZONE 'Asia/Kolkata')::date + CAST(:decision_days AS int) < :today))
ORDER BY coalesce(p.submitted_at, p.created_at)
LIMIT :limit
"""
)


_QUEUE_ACTIONS = text(
    """
SELECT a.id, a.created_at, a.approval_status, a.completion_status, a.start_at, a.end_at, a.outcome_review_due_at,
       a.proposed_by, a.approved_by, a.completed_by, a.assignee_id, a.region_id, a.station_id, p.category_label,
       p.evidence_snapshot_id, cardinality(a.grid_cell_ids) AS cells, r.code AS region_code, s.code AS station_code,
       asg.username AS assignee, pu.username AS proposer, o.status AS outcome_status, o.created_by AS reviewer_id,
       ou.username AS reviewer, es.section AS snap_section, es.created_by AS snap_author, es.region_ids AS snap_region_ids,
       es.station_ids AS snap_station_ids, es.may_mask AS snap_may_mask
FROM core.operational_action a
JOIN core.organizational_unit r ON r.id = a.region_id
JOIN core.organizational_unit s ON s.id = a.station_id
LEFT JOIN core.pattern_review p ON p.id = a.pattern_review_id
LEFT JOIN core.evidence_snapshot es ON es.id = p.evidence_snapshot_id
LEFT JOIN core.outcome_review o ON o.action_id = a.id
LEFT JOIN core.app_user asg ON asg.id = a.assignee_id
LEFT JOIN core.app_user pu ON pu.id = a.proposed_by
LEFT JOIN core.app_user ou ON ou.id = o.created_by
WHERE ((a.approval_status = 'PROPOSED' AND a.proposed_by <> :me
        AND (:g OR a.region_id = ANY (:r) OR a.station_id = ANY (:s)))
    OR (a.approval_status = 'APPROVED' AND a.completion_status = 'NOT_STARTED' AND a.start_at <= :today
        AND a.approved_by <> :me AND (:cg OR a.region_id = ANY (:cr) OR a.station_id = ANY (:cs))))
  AND (CAST(:region AS text) IS NULL OR r.code = :region)
  AND (NOT :mine OR a.assignee_id = :me)
  AND (NOT :overdue OR a.end_at < :today)
ORDER BY a.end_at, a.created_at
LIMIT :limit
"""
)

_QUEUE_OUTCOMES = text(
    """
SELECT a.id, a.created_at, a.approval_status, a.completion_status, a.start_at, a.end_at, a.outcome_review_due_at,
       a.proposed_by, a.approved_by, a.completed_by, a.assignee_id, a.region_id, a.station_id, p.category_label,
       p.evidence_snapshot_id, cardinality(a.grid_cell_ids) AS cells, r.code AS region_code, s.code AS station_code,
       asg.username AS assignee, pu.username AS proposer, o.status AS outcome_status, o.created_by AS reviewer_id,
       ou.username AS reviewer, es.section AS snap_section, es.created_by AS snap_author, es.region_ids AS snap_region_ids,
       es.station_ids AS snap_station_ids, es.may_mask AS snap_may_mask
FROM core.operational_action a
JOIN core.organizational_unit r ON r.id = a.region_id
JOIN core.organizational_unit s ON s.id = a.station_id
LEFT JOIN core.pattern_review p ON p.id = a.pattern_review_id
LEFT JOIN core.evidence_snapshot es ON es.id = p.evidence_snapshot_id
LEFT JOIN core.outcome_review o ON o.action_id = a.id
LEFT JOIN core.app_user asg ON asg.id = a.assignee_id
LEFT JOIN core.app_user pu ON pu.id = a.proposed_by
LEFT JOIN core.app_user ou ON ou.id = o.created_by
WHERE a.completion_status = 'COMPLETED' AND a.completed_by <> :me
  AND (o.id IS NULL OR (o.status = 'DRAFT' AND o.created_by = :me))
  AND (:g OR a.region_id = ANY (:r) OR a.station_id = ANY (:s))
  AND (CAST(:region AS text) IS NULL OR r.code = :region)
  AND (NOT :mine OR o.created_by = :me)
  AND (NOT :overdue OR a.outcome_review_due_at < :today)
ORDER BY a.outcome_review_due_at, a.created_at
LIMIT :limit
"""
)

QueueName = Literal["patterns", "actions", "outcomes"]
Priority = Literal["HIGH", "MEDIUM", "LOW"]


def _by_date(due: date, today: date) -> Priority:
    return "HIGH" if due < today else "MEDIUM" if due <= today + timedelta(days=7) else "LOW"


async def collect_queue(
    ctx: Ctx,
    s: AsyncSession,
    queue: QueueName,
    *,
    region: str | None = None,
    mine: bool = False,
    overdue: bool = False,
) -> tuple[list[QueueItem], bool]:
    """The items in one queue for this caller, highest priority first, and whether there are more
    than QUEUE_LIMIT (also used by the badge)."""
    today = _today()
    me = ctx.user.id
    items: list[QueueItem] = []
    filters = {
        "me": me,
        "limit": QUEUE_LIMIT + 1,
        "region": region,
        "mine": mine,
        "overdue": overdue,
        "today": today,
    }
    if queue == "patterns":
        decide = permission_scope(ctx.subject, "pattern_review.decide")
        rows = (
            await s.execute(
                _QUEUE_REVIEWS, {**filters, "decision_days": DECISION_DUE_DAYS, **_in_scope(decide)}
            )
        ).all()
        for r in rows[:QUEUE_LIMIT]:
            target = Target(region_id=r.region_id, station_id=r.station_id)
            mine = r.created_by == me
            due: date | None
            if r.supervisor_status == "PENDING":
                if (
                    mine
                    or not authorize(ctx.subject, "pattern_review.decide", target=target).allowed
                ):
                    continue
                nxt: Any = "DECIDE"
                due = r.submitted_at.astimezone(IST).date() + timedelta(days=DECISION_DUE_DAYS)
                age = (today - r.submitted_at.astimezone(IST).date()).days
                priority: Priority = (
                    "HIGH"
                    if due < today or r.record_count >= 50
                    else "MEDIUM"
                    if age >= 3
                    else "LOW"
                )
            else:
                if not authorize(ctx.subject, "pattern_review.create", target=target).allowed:
                    continue
                nxt, due = ("RESUBMIT" if r.supervisor_status == "RETURNED" else "SUBMIT"), None
                # Your own drafts: MEDIUM when returned (someone is waiting for you), else LOW.
                priority = "MEDIUM" if r.supervisor_status == "RETURNED" else "LOW"
            visible = evidence_visible(ctx, r)
            items.append(
                QueueItem(
                    type="PATTERN_REVIEW",
                    id=r.id,
                    priority=priority,
                    opened_at=r.submitted_at or r.created_at,
                    region=r.region_code,
                    station=r.station_code,
                    cells=r.cells,
                    category=r.category_label if visible else None,
                    owner=r.owner,
                    mine=mine,
                    due=due,
                    overdue=due is not None and due < today,
                    status=r.supervisor_status or r.analyst_status,
                    evidence_snapshot_id=r.evidence_snapshot_id if visible else None,
                    next_step=nxt,
                )
            )
        return _sorted(items), len(rows) > QUEUE_LIMIT
    if queue == "actions":
        approve = permission_scope(ctx.subject, "action.approve")
        complete = permission_scope(ctx.subject, "action.complete")
        rows = (
            await s.execute(
                _QUEUE_ACTIONS,
                {
                    **filters,
                    **_in_scope(approve),
                    "cg": complete.is_global,
                    "cr": list(complete.region_ids),
                    "cs": list(complete.station_ids),
                },
            )
        ).all()
    else:
        review = permission_scope(ctx.subject, "outcome.review")
        rows = (await s.execute(_QUEUE_OUTCOMES, {**filters, **_in_scope(review)})).all()
    for r in rows[:QUEUE_LIMIT]:
        target = Target(region_id=r.region_id, station_id=r.station_id, assignee_id=r.assignee_id)
        visible = evidence_visible(ctx, r)
        common: dict[str, Any] = {
            "id": r.id,
            "opened_at": r.created_at,
            "region": r.region_code,
            "station": r.station_code,
            "cells": r.cells,
            "category": r.category_label if visible else None,
            "evidence_snapshot_id": r.evidence_snapshot_id if visible else None,
        }
        if queue == "actions":
            if r.approval_status == "PROPOSED":
                if not authorize(ctx.subject, "action.approve", target=target).allowed:
                    continue
                nxt, status = "APPROVE", "PROPOSED"
            else:
                if r.completion_status != "NOT_STARTED" or r.approved_by == me:
                    continue
                if not authorize(ctx.subject, "action.complete", target=target).allowed:
                    continue
                if r.start_at > today:  # cannot be completed yet
                    continue
                nxt, status = "COMPLETE", "APPROVED"
            items.append(
                QueueItem(
                    type="ACTION",
                    priority=_by_date(r.end_at, today),
                    owner=r.assignee or r.proposer,
                    mine=r.assignee_id == me,
                    due=r.end_at,
                    overdue=r.end_at < today,
                    status=status,
                    next_step=nxt,
                    **common,
                )
            )
        else:
            if not authorize(ctx.subject, "outcome.review", target=target).allowed:
                continue
            due = r.outcome_review_due_at
            items.append(
                QueueItem(
                    type="OUTCOME_DUE",
                    priority=_by_date(due, today),
                    owner=r.reviewer,
                    mine=r.reviewer_id == me,
                    due=due,
                    overdue=due < today,
                    status=r.outcome_status or "DUE",
                    next_step="CONTINUE_OUTCOME"
                    if r.outcome_status == "DRAFT"
                    else "START_OUTCOME",
                    **common,
                )
            )
    return _sorted(items), len(rows) > QUEUE_LIMIT


def _sorted(items: list[QueueItem]) -> list[QueueItem]:
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(items, key=lambda i: (order[i.priority], i.due or date.max, i.opened_at))


@router.get("/review-queue")
async def review_queue(
    ctx: AuthenticatedCtx,
    queue: QueueName,
    priority: Priority | None = None,
    region: Annotated[str | None, Query(pattern=r"^[A-Z]{2,5}$")] = None,
    mine: bool = False,
    overdue: bool = False,
) -> QueueOut:
    """Items waiting for this caller, with the next step they may take (each item's history is at
    ``/workflow-history``). Pattern reviews: awaiting your decision, or your drafts and returned
    reviews. Actions: awaiting your approval, or approved, started and waiting for completion where
    you may complete them. Outcomes: completed actions (not by you) without an outcome review, or
    your draft (due 28 days after the action ends). Filters: priority, region, owned by you,
    overdue."""
    async with ctx.uow() as uow:
        items, more = await collect_queue(
            ctx, uow.session, queue, region=region, mine=mine, overdue=overdue
        )
        items = [i for i in items if priority is None or i.priority == priority]
        uow.audit(
            ctx.event(
                "RECORD_VIEW",
                object_type="review_queue",
                object_id=queue,
                filters={"priority": priority, "region": region, "mine": mine, "overdue": overdue},
                after_meta={"count": len(items), "more": more},
            )
        )
        await uow.commit()
    return QueueOut(queue=queue, data=items, more=more)


class HistoryItem(BaseModel):
    at: datetime
    actor: str | None
    from_state: str | None
    to_state: str
    rationale: str | None


class HistoryOut(BaseModel):
    data: list[HistoryItem]


HistoryType = Literal[
    "PATTERN_REVIEW", "ACTION", "OUTCOME_REVIEW", "DQ_ISSUE", "OPERATIONAL_RECORD"
]

# Data-quality issues and operational records keep their history in the audit log (Phases 2 and 3):
# allowed changes to that item, in order. States only; notes are never in the audit log.
_AUDIT_HISTORY = text(
    """
SELECT e.occurred_at AS created_at, u.username, e.action, e.before_meta, e.after_meta
FROM audit.audit_event e LEFT JOIN core.app_user u ON u.id = e.actor_user_id
WHERE e.object_type = :t AND e.object_id = :id AND e.result = 'ALLOW' AND e.action = ANY (:actions)
ORDER BY e.seq
"""
)
_AUDIT_ACTIONS = {
    "DQ_ISSUE": ("dq_issue", ["DQ_ISSUE_FLAG", "DQ_ISSUE_UPDATE"], "state"),
    "OPERATIONAL_RECORD": (
        "operational_record",
        ["OPERATIONAL_RECORD_CREATE", "OPERATIONAL_RECORD_REVIEW"],
        "review_status",
    ),
}


async def _visible_audit_subject(
    ctx: Ctx, s: AsyncSession, object_type: str, object_id: UUID
) -> None:
    """The caller may see this DQ issue or operational record (else an audited 404)."""
    if object_type == "DQ_ISSUE":
        row = (
            await s.execute(
                text(
                    "SELECT region_id, station_id, subject_type FROM core.data_quality_issue "
                    "WHERE id = :id"
                ),
                {"id": object_id},
            )
        ).one_or_none()
        # Same rule as the DQ queue (routes/dq.py): a source-level issue has no territory and is
        # visible to every dq.manage holder; any other issue needs dq.manage over its territory.
        source_level = (
            row is not None
            and row.subject_type == "SOURCE_SYSTEM"
            and row.region_id is None
            and row.station_id is None
        )
        allowed = row is not None and (
            permission_scope(ctx.subject, "dq.manage").any
            if source_level
            else authorize(
                ctx.subject,
                "dq.manage",
                target=Target(region_id=row.region_id, station_id=row.station_id),
            ).allowed
        )
        name = "dq_issue"
    else:
        row = (
            await s.execute(
                text(
                    "SELECT region_id, station_id, created_by FROM core.operational_record WHERE id = :id"
                ),
                {"id": object_id},
            )
        ).one_or_none()
        allowed = row is not None and (
            row.created_by == ctx.user.id
            or authorize(
                ctx.subject,
                "opsrecord.approve",
                target=Target(region_id=row.region_id, station_id=row.station_id),
            ).allowed
        )
        name = "operational_record"
    if not allowed:
        raise await _not_found(ctx, name, object_id)


@router.get("/workflow-history")
async def workflow_history(
    ctx: AuthenticatedCtx, object_type: HistoryType, object_id: UUID
) -> HistoryOut:
    """Who did what, when and why, for one Review Queue item the caller may see."""
    async with ctx.uow() as uow:
        s = uow.session
        if object_type in _AUDIT_ACTIONS:
            await _visible_audit_subject(ctx, s, object_type, object_id)
            audit_type, actions, key = _AUDIT_ACTIONS[object_type]
            rows = (
                await s.execute(
                    _AUDIT_HISTORY, {"t": audit_type, "id": str(object_id), "actions": actions}
                )
            ).all()
            data = [
                HistoryItem(
                    at=r.created_at,
                    actor=r.username,
                    from_state=(r.before_meta or {}).get(key),
                    to_state=str((r.after_meta or {}).get(key) or "OPEN"),
                    rationale=None,
                )
                for r in rows
            ]
        else:
            if object_type == "PATTERN_REVIEW":
                await _load_review(ctx, s, object_id)
            elif object_type == "ACTION":
                await _load_action(ctx, s, object_id)
            else:
                await _load_outcome(ctx, s, object_id)
            rows = (
                await s.execute(
                    text(
                        "SELECT w.created_at, u.username, w.from_state, w.to_state, w.rationale FROM core.workflow_transition w "
                        "LEFT JOIN core.app_user u ON u.id = w.actor_id WHERE w.object_type = :t AND w.object_id = :id ORDER BY w.id"
                    ),
                    {"t": object_type, "id": object_id},
                )
            ).all()
            data = [
                HistoryItem(
                    at=r.created_at,
                    actor=r.username,
                    from_state=r.from_state,
                    to_state=r.to_state,
                    rationale=r.rationale,
                )
                for r in rows
            ]
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="workflow_history", object_id=str(object_id))
        )
        await uow.commit()
    return HistoryOut(data=data)
