"""Review Queue badge (global shell): how many items wait for this caller, nothing else.

Counts only, in the same scope as the queues themselves: open or assigned data-quality issues
within ``dq.manage``, and pending operational records the caller may decide (another author, a
kind their approve grant covers, a subject incident they can see), and the Phase 6 workflow
queues (pattern reviews, actions, outcome reviews; the same function as the queues). It returns no record content, so
it is not audited per call (it is requested on every page); opening a queue is audited as usual.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from crimemap_api.deps import AUTHENTICATED, Ctx
from crimemap_api.policy.engine import Target, permission_scope
from crimemap_api.routes.opsrecords import _SELECT, _may_approve, _subject_visible
from crimemap_api.routes.workflow import QueueName, collect_queue

router = APIRouter(prefix="/api/v1", tags=["review-queue"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]

PENDING_SCAN = 200  # the badge says "200+" beyond this

_DQ_OPEN = text(
    """
SELECT count(*) FROM core.data_quality_issue q
WHERE q.state IN ('OPEN', 'ASSIGNED')
  AND (:global OR q.region_id = ANY(:regions) OR q.station_id = ANY(:stations)
       OR (q.subject_type = 'SOURCE_SYSTEM' AND q.region_id IS NULL AND q.station_id IS NULL))
"""
)


class QueueCounts(BaseModel):
    data_quality: int | None  # None: the caller has no data-quality queue
    operational_records: int | None  # None: the caller reviews no operational records
    more: bool  # items beyond the scanned limits (operational records or a workflow queue)
    pattern_reviews: int | None = None  # None: the caller has no such queue
    actions: int | None = None
    outcomes: int | None = None
    ai_insights: int | None = None  # pending insights the caller may review


WORKFLOW_QUEUES: dict[QueueName, tuple[str, ...]] = {
    "patterns": ("pattern_review.create", "pattern_review.decide"),
    "actions": ("action.approve", "action.complete"),
    "outcomes": ("outcome.review",),
}


@router.get("/review-queue/summary")
async def review_queue_summary(ctx: AuthenticatedCtx) -> QueueCounts:
    dq = permission_scope(ctx.subject, "dq.manage")
    approve = permission_scope(ctx.subject, "opsrecord.approve")
    dq_count: int | None = None
    ops_count: int | None = None
    more = False
    async with ctx.uow() as uow:
        if dq.any:
            dq_count = (
                await uow.session.execute(
                    _DQ_OPEN,
                    {
                        "global": dq.is_global,
                        "regions": list(dq.region_ids),
                        "stations": list(dq.station_ids),
                    },
                )
            ).scalar_one()
        if approve.any:
            rows = (
                await uow.session.execute(
                    _SELECT,
                    {
                        "id": None,
                        "status": "PENDING",
                        "kind": None,
                        "g": approve.is_global,
                        "r": list(approve.region_ids),
                        "s": list(approve.station_ids),
                        "limit": PENDING_SCAN + 1,
                    },
                )
            ).all()
            more = len(rows) > PENDING_SCAN
            ops_count = sum(
                1
                for r in rows[:PENDING_SCAN]
                if r.created_by != ctx.user.id
                and _subject_visible(ctx, r)
                and _may_approve(
                    ctx, Target(region_id=r.region_id, station_id=r.station_id), r.record_kind
                )
            )
        workflow: dict[str, int | None] = {}
        for queue, permissions in WORKFLOW_QUEUES.items():
            if any(permission_scope(ctx.subject, p).any for p in permissions):
                found, truncated = await collect_queue(ctx, uow.session, queue)
                workflow[queue] = len(found)
                more = more or truncated
            else:
                workflow[queue] = None
        ai_count: int | None = None
        if permission_scope(ctx.subject, "ai.insight.review").any:
            from crimemap_api.routes.ai import pending_count

            ai_count, ai_more = await pending_count(ctx, uow.session)
            more = more or ai_more
        await uow.commit()
    return QueueCounts(
        ai_insights=ai_count,
        data_quality=dq_count,
        operational_records=ops_count,
        more=more,
        pattern_reviews=workflow["patterns"],
        actions=workflow["actions"],
        outcomes=workflow["outcomes"],
    )
