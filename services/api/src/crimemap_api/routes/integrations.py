"""Source-system health and ingestion reconciliation (``lineage.read``).

Read through owner-privileged views that expose counts and timestamps only: the API role has no
access to the staging schema, so raw payloads can never be returned from here. Sources are not
territorial, so a region-scoped grant of ``lineage.read`` sees every source's health.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import text

from crimemap_api.deps import AUTHENTICATED, Ctx, Requirement
from crimemap_api.http import ProblemError

router = APIRouter(prefix="/api/v1", tags=["integrations"])

LineageCtx = Annotated[Ctx, Depends(Requirement("lineage.read"))]
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]


class LastBatch(BaseModel):
    batch_ref: str
    received_at: datetime
    record_count: int
    accepted: int
    duplicates: int
    rejected: int
    status: str


class SourceOut(BaseModel):
    code: str
    name: str
    approved_status: str
    adapter_type: str
    last_batch_at: datetime | None
    last_health_check_at: datetime | None
    last_health_status: str | None
    stale_after_hours: int | None
    open_dead_letters: int
    last_batch: LastBatch | None
    is_connected: bool


class SourceList(BaseModel):
    data: list[SourceOut]


class ReconciliationRow(BaseModel):
    source_code: str
    received_on: date
    batches: int
    declared: int
    accepted: int
    duplicates: int
    rejected: int
    unexplained: int
    rejected_batches: int


class ReconciliationList(BaseModel):
    data: list[ReconciliationRow]


_SOURCES = text("SELECT * FROM core.source_health ORDER BY approved_status, code")
_SOURCE = text("SELECT * FROM core.source_health WHERE code = :code")


def _source(r: object) -> SourceOut:
    m = r._mapping  # type: ignore[attr-defined]
    last = (
        LastBatch(
            batch_ref=m["last_batch_ref"],
            received_at=m["last_received_at"],
            record_count=m["last_record_count"],
            accepted=m["last_accepted"],
            duplicates=m["last_duplicates"],
            rejected=m["last_rejected"],
            status=m["last_batch_status"],
        )
        if m["last_batch_ref"] is not None
        else None
    )
    return SourceOut(
        code=m["code"],
        name=m["name"],
        approved_status=m["approved_status"],
        adapter_type=m["adapter_type"],
        last_batch_at=m["last_batch_at"],
        last_health_check_at=m["last_health_check_at"],
        last_health_status=m["last_health_status"],
        stale_after_hours=m["stale_after_hours"],
        open_dead_letters=m["open_dead_letters"],
        last_batch=last,
        # Only demo feeds exist; every real government system is an unconnected interface stub.
        is_connected=m["approved_status"] in ("DEMO", "APPROVED"),
    )


@router.get("/integrations/sources")
async def list_sources(ctx: LineageCtx) -> SourceList:
    async with ctx.uow() as uow:
        rows = (await uow.session.execute(_SOURCES)).all()
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="source_system", after_meta={"count": len(rows)})
        )
        await uow.commit()
    return SourceList(data=[_source(r) for r in rows])


@router.get("/integrations/sources/{code}")
async def get_source(
    ctx: LineageCtx, code: Annotated[str, Path(pattern=r"^[A-Z0-9_]{2,40}$")]
) -> SourceOut:
    async with ctx.uow() as uow:
        row = (await uow.session.execute(_SOURCE, {"code": code})).one_or_none()
        if row is None:
            raise ProblemError(404, "not-found", "Unknown source system")
        uow.audit(ctx.event("RECORD_VIEW", object_type="source_system", object_id=code))
        await uow.commit()
    return _source(row)


@router.get("/integrations/reconciliation")
async def reconciliation(
    ctx: LineageCtx, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> ReconciliationList:
    async with ctx.uow() as uow:
        rows = (
            await uow.session.execute(
                text(
                    "SELECT * FROM core.ingest_reconciliation "
                    "WHERE received_on >= current_date - CAST(:days AS int) "
                    "ORDER BY received_on DESC, source_code"
                ),
                {"days": days},
            )
        ).all()
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="ingest_reconciliation", filters={"days": days})
        )
        await uow.commit()
    return ReconciliationList(data=[ReconciliationRow(**r._mapping) for r in rows])


class FreshSource(BaseModel):
    code: str
    name: str
    last_batch_at: datetime | None
    status: str | None


class FreshnessOut(BaseModel):
    data_label: str | None
    last_refreshed_at: datetime | None
    sources: list[FreshSource]


@router.get("/data-freshness")
async def data_freshness(ctx: AuthenticatedCtx) -> FreshnessOut:
    """When the active feeds last delivered data, for the always-visible safeguard strip. Exposes
    feed names, times and health status only: no counts, territories or records."""
    async with ctx.services.sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT code, name, last_batch_at, last_health_status AS status, approved_status "
                    "FROM core.source_system WHERE approved_status IN ('DEMO', 'APPROVED') ORDER BY code"
                )
            )
        ).all()
    stamps = [r.last_batch_at for r in rows if r.last_batch_at is not None]
    return FreshnessOut(
        data_label="DEMO / SYNTHETIC DATA"
        if any(r.approved_status == "DEMO" for r in rows)
        else None,
        last_refreshed_at=max(stamps) if stamps else None,
        sources=[
            FreshSource(code=r.code, name=r.name, last_batch_at=r.last_batch_at, status=r.status)
            for r in rows
        ],
    )
