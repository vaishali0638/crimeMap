"""Controlled exports (Phase 5, spec "REPORTS"): aggregated CSV and a PDF briefing.

- Needs ``report.export.aggregate_csv`` or ``report.export.pdf`` for the analysis's region, and a
  fresh sign-in (re-authentication, ⊕ in the role matrix); refusals are audited.
- The export re-runs the analysis and cites a new evidence snapshot, so the file can be traced to
  exactly the data it contains. Only aggregated tables are exported, never record-level rows.
- Each file carries the watermark ``INTERNAL / RESTRICTED`` and the required metadata (requesting
  user, time, filters, classification, source and refresh, data quality, method, expiry).
- Files are readable only by their requester (row-level security), expire after 24 hours and are
  deleted by the worker. Request, generation and each download are audited.
- Map-image (PNG/PDF) export needs server-side map rendering and is not available yet.
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from crimemap_api.analytics.query import AnalyticsQuery, Section
from crimemap_api.audit.events import write_independent
from crimemap_api.deps import AUTHENTICATED, Ctx
from crimemap_api.http import ProblemError
from crimemap_api.policy.catalogue import Purpose
from crimemap_api.policy.engine import Target
from crimemap_api.reports.render import render_csv, render_pdf
from crimemap_api.routes.analytics import run_section, store_snapshot
from crimemap_api.routes.map import IST
from crimemap_api.territory import load_region

router = APIRouter(prefix="/api/v1", tags=["reports"])
AuthenticatedCtx = Annotated[Ctx, Depends(AUTHENTICATED)]

PERMISSIONS = {"CSV": "report.export.aggregate_csv", "PDF": "report.export.pdf"}
MEDIA = {"CSV": "text/csv; charset=utf-8", "PDF": "application/pdf"}
EXPIRY = timedelta(hours=24)
MAX_BYTES = 5 * 1024 * 1024  # core.report_export CHECK


def _region_wide(ctx: Ctx, permission: str, region_id: UUID) -> bool:
    """Region-wide grants are checked up front; station-scoped ones after the coverage is known."""
    from crimemap_api.policy.engine import permission_scope

    scope = permission_scope(ctx.subject, permission)
    return scope.is_global or region_id in scope.region_ids


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["CSV", "PDF"]
    section: Section
    params: AnalyticsQuery
    # Exports need a declared purpose and a fresh sign-in (role matrix ⊕; policy engine).
    purpose: Literal["BRIEFING", "OPERATIONAL_REVIEW", "PATTERN_ANALYSIS", "OUTCOME_REVIEW"]


class ReportOut(BaseModel):
    id: UUID
    format: str
    section: str
    filename: str
    size_bytes: int
    content_sha256: str
    classification: str
    created_at: Any
    expires_at: Any
    downloads: int
    evidence_snapshot_id: UUID


class ReportList(BaseModel):
    data: list[ReportOut]


_INSERT = text(
    """
INSERT INTO core.report_export (format, section, filename, content, content_sha256, classification, metadata,
                                evidence_snapshot_id, region_id, created_by, expires_at)
VALUES (:format, :section, :filename, :content, :sha, 'RESTRICTED', CAST(:metadata AS jsonb), :snapshot, :region,
        :user, now() + interval '24 hours')
RETURNING id
"""
)

_SELECT = text(
    """
SELECT id, format, section, filename, octet_length(content) AS size_bytes, content_sha256, classification,
       created_at, expires_at, downloads, evidence_snapshot_id
FROM core.report_export
WHERE expires_at > now() AND (CAST(:id AS uuid) IS NULL OR id = :id)
ORDER BY created_at DESC LIMIT 50
"""
)


def _out(row: Any) -> ReportOut:
    return ReportOut(**row._mapping)


@router.post("/reports", status_code=201)
async def create_report(ctx: AuthenticatedCtx, body: ReportIn) -> JSONResponse:
    permission = PERMISSIONS[body.format]
    async with ctx.uow() as uow:
        session = uow.session
        units = await load_region(session, body.params.region)
        if units is None:
            raise ProblemError(422, "validation-error", "Unknown region")
        purpose = Purpose(body.purpose)
        # Purpose and fresh sign-in first (cheap, audited refusals), on the requested region...
        if _region_wide(ctx, permission, units.region.id):
            ctx.require(permission, target=Target(region_id=units.region.id), purpose=purpose)
        data, meta = await run_section(ctx, session, body.section, body.params)
        # ...then the export permission over every unit the result covers (a station-scoped SHO
        # exports their stations; a comparison needs both sides). Refusals are audited.
        scope = meta["_scope"]
        stations = scope["station_ids"]
        targets = (
            [Target(region_id=UUID(rid)) for rid in scope["region_ids"]]
            if stations is None
            else [
                Target(region_id=UUID(scope["region_ids"][0]), station_id=UUID(sid))
                for sid in stations
            ]
        )
        if not targets:
            raise ProblemError(422, "validation-error", "Nothing to export for this selection")
        for target in targets:
            ctx.require(permission, target=target, purpose=purpose)
        # Recorded in its own transaction, so the request stays in the log even if generation fails.
        await write_independent(
            ctx.services.engine,
            ctx.event(
                "EXPORT_REQUEST",
                object_type="report_export",
                filters=body.params.as_params(),
                purpose=body.purpose,
                after_meta={"format": body.format, "section": body.section},
            ),
            ctx.audit_ctx,
        )
        snapshot_id = await store_snapshot(ctx, session, body.section, body.params, data, meta)
        now = datetime.now(IST)
        who = {
            "user": ctx.user.username or ctx.user.name or str(ctx.user.id),
            "generated_at": now,
            "snapshot_id": str(snapshot_id),
        }
        content = (render_csv if body.format == "CSV" else render_pdf)(
            body.section, data, meta, **who
        )
        if len(content) > MAX_BYTES:
            raise ProblemError(
                413, "payload-too-large", "The export would exceed 5 MB; narrow the filters"
            )
        sha = hashlib.sha256(content).hexdigest()
        filename = (
            f"crimemap_{body.section}_{body.params.region}_{now:%Y%m%d_%H%M}.{body.format.lower()}"
        )
        report_id = (
            await session.execute(
                _INSERT,
                {
                    "format": body.format,
                    "section": body.section,
                    "filename": filename,
                    "content": content,
                    "sha": sha,
                    "metadata": json.dumps(
                        {
                            "filters": meta["filters_applied"],
                            "record_count": meta["record_count"],
                            "method": meta["method"],
                        }
                    ),
                    "snapshot": snapshot_id,
                    "region": units.region.id,
                    "user": ctx.user.id,
                },
            )
        ).scalar_one()
        uow.audit(
            ctx.event(
                "EXPORT_GENERATED",
                object_type="report_export",
                object_id=str(report_id),
                after_meta={
                    "format": body.format,
                    "section": body.section,
                    "bytes": len(content),
                    "sha256": sha,
                    "evidence_snapshot_id": str(snapshot_id),
                    "records": meta["record_count"],
                },
            )
        )
        row = (await session.execute(_SELECT, {"id": report_id})).one()
        await uow.commit()
    return JSONResponse(status_code=201, content=_out(row).model_dump(mode="json"))


@router.get("/reports")
async def list_reports(ctx: AuthenticatedCtx) -> ReportList:
    """The caller's own unexpired exports (row-level security: requester only)."""
    async with ctx.uow() as uow:
        rows = (await uow.session.execute(_SELECT, {"id": None})).all()
        uow.audit(
            ctx.event("RECORD_VIEW", object_type="report_export", after_meta={"count": len(rows)})
        )
        await uow.commit()
    return ReportList(data=[_out(r) for r in rows])


@router.get("/reports/{report_id}/download")
async def download_report(ctx: AuthenticatedCtx, report_id: UUID) -> Response:
    async with ctx.uow() as uow:
        row = (
            await uow.session.execute(
                text(
                    "SELECT format, filename, content, content_sha256 FROM core.report_export "
                    "WHERE id = :id AND expires_at > now()"
                ),
                {"id": report_id},
            )
        ).one_or_none()
        if row is None:
            raise await ctx.deny(
                ctx.event(
                    "EXPORT_DOWNLOAD",
                    "DENY",
                    reason="NOT_FOUND_OR_EXPIRED",
                    object_type="report_export",
                    object_id=str(report_id),
                ),
                404,
                "not-found",
                "Not found or expired",
            )
        await uow.session.execute(
            text("UPDATE core.report_export SET downloads = downloads + 1 WHERE id = :id"),
            {"id": report_id},
        )
        uow.audit(
            ctx.event(
                "EXPORT_DOWNLOAD",
                object_type="report_export",
                object_id=str(report_id),
                after_meta={"sha256": row.content_sha256},
            )
        )
        await uow.commit()
    return Response(
        content=bytes(row.content),
        media_type=MEDIA[row.format],
        headers={
            "Content-Disposition": f'attachment; filename="{row.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
