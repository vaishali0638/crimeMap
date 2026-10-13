"""Ingestion pipeline (docs/architecture.md §5, ADR-027), run as the crimemap_worker role.

Per batch, in one transaction (serialised per source by an advisory lock):
  verify signature -> check source approval -> detect a re-used batch reference -> stage raw records
  exactly as received (immutable, content-aware idempotency key) -> validate against the canonical
  contract (invalid or conflicting -> dead letter) -> store the normalised record -> mapping and
  spatial enrichment (newest version wins) -> locations (residential ones generalised) and
  restricted details -> lineage -> data-quality rules -> batch statistics -> one audit event.
The audit chain lock is taken only by the final audit insert, so the batch never blocks API audit
writes for long (cf. review finding B1). A batch that fails is rolled back, then recorded as FAILED
and audited in a separate transaction; it can be re-delivered and processed later.
"""

import asyncio
import hashlib
import json
import logging
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import exc as sa_exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from crimemap_api.audit.events import AuditContext, AuditEvent, insert_event, write_independent
from crimemap_api.ingest.contract import SourceRecord
from crimemap_api.ingest.signing import (
    MAX_BATCH_BYTES,
    BatchSignatureError,
    canonical_json,
    read_batch,
)
from crimemap_worker.adapters import RUNNABLE_STATUSES, adapter_for

log = logging.getLogger("crimemap.ingest")

CONTRACT = "source-record/1"


@dataclass(frozen=True)
class IngestResult:
    batch_ref: str
    source_code: str
    # PROCESSED | ALREADY_PROCESSED | REJECTED_SIGNATURE | REJECTED_SOURCE | REJECTED_BATCH_REF | FAILED
    status: str
    record_count: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    superseded: int = 0
    issues_opened: int = 0


def idempotency_key(source_code: str, record: dict[str, Any], content_hash: str) -> str:
    """Same source, reference, version *and content* = the same record. A different payload under
    the same version gets a different key, is staged, and is then dead-lettered as a version
    conflict (change detection) unless the earlier one was invalid (a correction)."""
    ref, version = record.get("source_record_reference"), record.get("source_version")
    if isinstance(ref, str) and isinstance(version, int) and not isinstance(version, bool):
        return hashlib.sha256(f"{source_code}|{ref}|{version}|{content_hash}".encode()).hexdigest()
    return "unkeyed:" + content_hash  # unidentifiable records are still staged (then dead-lettered)


def _error_summary(exc: ValidationError) -> str:
    """Field paths and messages only — never the submitted values."""
    parts = [
        f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    ]
    return "; ".join(parts)[:1000]


def _file_digest(path: Path) -> str:
    """SHA-256 of a delivered file, read in chunks and capped (oversized files are hashed only up
    to the batch size limit; they are rejected anyway)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = MAX_BATCH_BYTES + 1
        while remaining > 0 and (chunk := f.read(min(1 << 20, remaining))):
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _claimed_source(path: Path) -> str | None:
    """The source an *unverified* file claims to come from (only used to attribute a rejection)."""
    try:
        if path.stat().st_size > MAX_BATCH_BYTES:
            return None
        claimed = json.loads(path.read_bytes())["envelope"]["source_system"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return claimed if isinstance(claimed, str) and len(claimed) <= 64 else None


async def _reject(
    engine: AsyncEngine,
    path: Path,
    reason: str,
    environment: str,
    *,
    code: str = "SIGNATURE_INVALID",
    status: str = "REJECTED_SIGNATURE",
) -> IngestResult:
    digest = await asyncio.to_thread(_file_digest, path)
    if code == "SIGNATURE_INVALID":
        # Count the rejection against the source it claims, so reconciliation and the health page
        # show it. Nothing from the file is staged.
        claimed = await asyncio.to_thread(_claimed_source, path)
        if claimed is not None:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO staging.ingest_batch (source_system_id, batch_ref, signature_valid, "
                        "record_count, status, content_sha256, completed_at) "
                        "SELECT id, :ref, false, 0, 'REJECTED_SIGNATURE', :h, now() "
                        "FROM core.source_system WHERE code = :c "
                        "ON CONFLICT (source_system_id, batch_ref) DO NOTHING"
                    ),
                    {"ref": f"unverified:{digest[:16]}", "h": digest, "c": claimed},
                )
    await write_independent(
        engine,
        AuditEvent(
            action="INGEST_REJECTED",
            result="DENY",
            reason=code,
            object_type="ingest_batch",
            after_meta={"file_sha256_16": digest[:16], "detail": reason},
        ),
        AuditContext(request_id=f"ingest:unverified:{digest[:16]}", environment=environment),
    )
    log.warning("ingest_batch_rejected", extra={"file_sha256_16": digest[:16], "reason": code})
    return IngestResult(batch_ref=f"unverified:{digest[:16]}", source_code="?", status=status)


def is_transient(error: BaseException) -> bool:
    """Errors worth retrying later (database or network unavailable), as opposed to a batch that
    can never succeed as delivered."""
    if isinstance(error, sa_exc.DBAPIError) and error.connection_invalidated:
        return True
    return isinstance(
        error,
        sa_exc.OperationalError
        | sa_exc.InterfaceError
        | sa_exc.TimeoutError
        | ConnectionError
        | TimeoutError,
    )


async def _record_failure(
    engine: AsyncEngine,
    *,
    source_code: str,
    batch_ref: str,
    content_hash: str,
    record_count: int,
    error: BaseException,
    environment: str,
) -> None:
    """After a rollback: mark the batch FAILED (so health and reconciliation show it) and audit it.
    Only the error type and SQLSTATE are recorded — database messages can contain values."""
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO staging.ingest_batch (source_system_id, batch_ref, signature_valid, "
                    "record_count, status, content_sha256, completed_at) "
                    "SELECT id, :b, true, :n, 'FAILED', :h, now() FROM core.source_system WHERE code = :c "
                    "ON CONFLICT (source_system_id, batch_ref) DO UPDATE SET status = 'FAILED', "
                    "completed_at = now() WHERE staging.ingest_batch.status <> 'PROCESSED'"
                ),
                {"b": batch_ref, "n": record_count, "h": content_hash, "c": source_code},
            )
        await write_independent(
            engine,
            AuditEvent(
                action="INGEST_FAILED",
                result="ERROR",
                reason=type(error).__name__,
                object_type="ingest_batch",
                after_meta={"source": source_code, "batch_ref": batch_ref, "sqlstate": sqlstate},
            ),
            AuditContext(request_id=f"ingest:{batch_ref}", environment=environment),
        )
    except Exception:
        log.exception("ingest_failure_not_recorded", extra={"batch_ref": batch_ref})
    log.error(
        "ingest_batch_failed",
        extra={"batch_ref": batch_ref, "error": type(error).__name__, "sqlstate": sqlstate},
    )


# Statements are written out in full, without string composition, so static analysis can check them
# as literals (as in Phase 1). The `latest` CTE (newest valid version of each record in the batch,
# read from the validated, normalised `canonical` column) is therefore repeated where needed.
_UPSERT_INCIDENTS = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (r.canonical->>'source_record_reference') r.id AS raw_id, r.canonical AS p
    FROM staging.raw_ingest r
    WHERE r.batch_id = :batch AND r.status = 'VALID'
    ORDER BY r.canonical->>'source_record_reference', (r.canonical->>'source_version')::int DESC
),
src AS (
    SELECT l.raw_id, l.p, CASE WHEN l.p->'location'->>'lat' IS NOT NULL THEN ST_SetSRID(ST_MakePoint(
        (l.p->'location'->>'lon')::float8, (l.p->'location'->>'lat')::float8), 4326) END AS geom,
           rs.id AS reporting_station_id, rs.parent_id AS reporting_region_id,
           coalesce(l.p->>'official_offence_section', l.p->>'event_type_code') AS code
    FROM latest l
    LEFT JOIN core.organizational_unit rs
           ON rs.unit_type = 'STATION' AND rs.code = l.p->>'reporting_station_code'
),
geo AS (
    SELECT src.*, beat.org_unit_id AS beat_id, st.id AS station_id, st.parent_id AS region_id
    FROM src
    LEFT JOIN LATERAL (
        SELECT b.org_unit_id FROM core.current_boundary b
        WHERE src.geom IS NOT NULL AND b.unit_type = 'BEAT' AND ST_Covers(b.geometry, src.geom)
        ORDER BY b.code LIMIT 1
    ) beat ON true
    LEFT JOIN core.organizational_unit bo ON bo.id = beat.org_unit_id
    LEFT JOIN core.organizational_unit st ON st.id = bo.parent_id
)
INSERT INTO core.incident_record (
    source_system_id, source_record_reference, source_version, record_type,
    official_offence_act, official_offence_section, official_offence_name, analytical_category_id,
    occurrence_start_at, occurrence_end_at, reported_at, status_code, reporting_station_id,
    occurrence_jurisdiction_id, region_id, station_id, beat_id, sensitivity_classification,
    access_classification, is_sensitive_category, record_quality_status, is_demo)
SELECT :source_id, g.p->>'source_record_reference', (g.p->>'source_version')::int, g.p->>'record_type',
       g.p->>'official_offence_act', coalesce(g.p->>'official_offence_section', g.p->>'event_type_code'),
       g.p->>'official_offence_name', cat.id,
       (g.p->>'occurrence_start_at')::timestamptz, (g.p->>'occurrence_end_at')::timestamptz,
       (g.p->>'reported_at')::timestamptz, g.p->>'status', g.reporting_station_id,
       coalesce(g.beat_id, g.station_id), coalesce(g.region_id, g.reporting_region_id), g.station_id,
       g.beat_id, g.p->>'sensitivity_classification', g.p->>'sensitivity_classification',
       coalesce(cat.is_sensitive, false), 'OK', :is_demo
FROM geo g
LEFT JOIN core.offence_category_map m ON m.mapping_version = :mapping AND m.offence_code = g.code
LEFT JOIN core.analytical_category cat ON cat.id = m.analytical_category_id
ON CONFLICT (source_system_id, source_record_reference) DO UPDATE SET
    source_version = EXCLUDED.source_version, record_type = EXCLUDED.record_type,
    official_offence_act = EXCLUDED.official_offence_act,
    official_offence_section = EXCLUDED.official_offence_section,
    official_offence_name = EXCLUDED.official_offence_name,
    analytical_category_id = EXCLUDED.analytical_category_id,
    occurrence_start_at = EXCLUDED.occurrence_start_at, occurrence_end_at = EXCLUDED.occurrence_end_at,
    reported_at = EXCLUDED.reported_at, status_code = EXCLUDED.status_code,
    reporting_station_id = EXCLUDED.reporting_station_id,
    occurrence_jurisdiction_id = EXCLUDED.occurrence_jurisdiction_id, region_id = EXCLUDED.region_id,
    station_id = EXCLUDED.station_id, beat_id = EXCLUDED.beat_id,
    sensitivity_classification = EXCLUDED.sensitivity_classification,
    access_classification = EXCLUDED.access_classification,
    is_sensitive_category = EXCLUDED.is_sensitive_category, updated_at = now()
WHERE core.incident_record.source_version < EXCLUDED.source_version
RETURNING id
"""
)

# Residential locations (HIGHLY_RESTRICTED when exact) never reach the analytical store: the exact
# point goes to the restricted schema, the core row holds a ~250 m grid point inside the assigned
# beat (core.generalise_point), and the free-text location is dropped. A reviewer's status survives
# a new source version that does not move the point.
_UPSERT_LOCATIONS = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (r.canonical->>'source_record_reference') r.id AS raw_id, r.canonical AS p
    FROM staging.raw_ingest r
    WHERE r.batch_id = :batch AND r.status = 'VALID'
    ORDER BY r.canonical->>'source_record_reference', (r.canonical->>'source_version')::int DESC
),
applied AS (
    SELECT i.id AS incident_id, i.beat_id, l.p,
           CASE WHEN l.p->'location'->>'lat' IS NOT NULL THEN ST_SetSRID(ST_MakePoint(
               (l.p->'location'->>'lon')::float8, (l.p->'location'->>'lat')::float8), 4326) END AS g,
           coalesce((l.p->'location'->>'is_residential')::boolean, false) AS res
    FROM latest l
    JOIN core.incident_record i ON i.source_system_id = :source_id
     AND i.source_record_reference = l.p->>'source_record_reference'
     AND i.source_version = (l.p->>'source_version')::int
    WHERE i.id = ANY(:ids)
)
INSERT INTO core.incident_location (incident_id, geometry, location_text, locality, precision_level,
                                    is_residential, confidence_score, geocode_method)
SELECT a.incident_id,
       CASE WHEN a.res THEN core.generalise_point(a.g, a.beat_id) ELSE a.g END,
       CASE WHEN a.res THEN NULL ELSE a.p->'location'->>'text' END,
       a.p->'location'->>'locality',
       CASE WHEN a.res AND a.g IS NOT NULL THEN 'GRID' WHEN a.res THEN 'UNKNOWN'
            ELSE coalesce(a.p->'location'->>'precision', 'UNKNOWN') END,
       a.res, (a.p->'location'->>'confidence')::numeric, coalesce(a.p->'location'->>'method', 'NONE')
FROM applied a
ON CONFLICT (incident_id) DO UPDATE SET
    reviewer_status = CASE WHEN core.incident_location.geometry IS NOT DISTINCT FROM EXCLUDED.geometry
                           THEN core.incident_location.reviewer_status ELSE 'UNREVIEWED' END,
    verified_at = CASE WHEN core.incident_location.geometry IS NOT DISTINCT FROM EXCLUDED.geometry
                       THEN core.incident_location.verified_at END,
    geometry = EXCLUDED.geometry, location_text = EXCLUDED.location_text,
    locality = EXCLUDED.locality, precision_level = EXCLUDED.precision_level,
    is_residential = EXCLUDED.is_residential, confidence_score = EXCLUDED.confidence_score,
    geocode_method = EXCLUDED.geocode_method
"""
)

# Two statements instead of ON CONFLICT DO UPDATE: the worker may write restricted details but can
# read only the key column, never the tokens (DO UPDATE would need SELECT on every column). A newer
# version that omits the details clears them.
_UPDATE_RESTRICTED = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (r.canonical->>'source_record_reference') r.id AS raw_id, r.canonical AS p
    FROM staging.raw_ingest r
    WHERE r.batch_id = :batch AND r.status = 'VALID'
    ORDER BY r.canonical->>'source_record_reference', (r.canonical->>'source_version')::int DESC
),
applied AS (
    SELECT i.id AS incident_id, l.p,
           CASE WHEN coalesce((l.p->'location'->>'is_residential')::boolean, false)
                 AND l.p->'location'->>'lat' IS NOT NULL
                THEN ST_SetSRID(ST_MakePoint((l.p->'location'->>'lon')::float8,
                                             (l.p->'location'->>'lat')::float8), 4326) END AS home
    FROM latest l
    JOIN core.incident_record i ON i.source_system_id = :source_id
     AND i.source_record_reference = l.p->>'source_record_reference'
     AND i.source_version = (l.p->>'source_version')::int
    WHERE i.id = ANY(:ids)
)
UPDATE restricted.sensitive_source_detail d
SET victim_reference_token = a.p->'restricted'->>'victim_reference_token',
    witness_reference_token = a.p->'restricted'->>'witness_reference_token',
    residential_location = a.home
FROM applied a WHERE d.incident_id = a.incident_id
"""
)

_INSERT_RESTRICTED = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (r.canonical->>'source_record_reference') r.id AS raw_id, r.canonical AS p
    FROM staging.raw_ingest r
    WHERE r.batch_id = :batch AND r.status = 'VALID'
    ORDER BY r.canonical->>'source_record_reference', (r.canonical->>'source_version')::int DESC
),
applied AS (
    SELECT i.id AS incident_id, l.p,
           CASE WHEN coalesce((l.p->'location'->>'is_residential')::boolean, false)
                 AND l.p->'location'->>'lat' IS NOT NULL
                THEN ST_SetSRID(ST_MakePoint((l.p->'location'->>'lon')::float8,
                                             (l.p->'location'->>'lat')::float8), 4326) END AS home
    FROM latest l
    JOIN core.incident_record i ON i.source_system_id = :source_id
     AND i.source_record_reference = l.p->>'source_record_reference'
     AND i.source_version = (l.p->>'source_version')::int
    WHERE i.id = ANY(:ids)
)
INSERT INTO restricted.sensitive_source_detail (incident_id, victim_reference_token,
                                                witness_reference_token, residential_location)
SELECT a.incident_id, a.p->'restricted'->>'victim_reference_token',
       a.p->'restricted'->>'witness_reference_token', a.home
FROM applied a
WHERE (jsonb_typeof(a.p->'restricted') = 'object' OR a.home IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM restricted.sensitive_source_detail d WHERE d.incident_id = a.incident_id)
"""
)

# A record that re-uses a reference and version already accepted with *different* content is a
# version conflict: the earlier record stands, the new one is dead-lettered for the source to fix.
_VERSION_CONFLICTS = text(
    """
WITH conflicted AS (
    UPDATE staging.raw_ingest r
    SET status = 'INVALID', processed_at = now(),
        error = 'VERSION_CONFLICT: this reference and version were already received with different content'
    WHERE r.batch_id = :batch AND r.status = 'VALID' AND EXISTS (
        SELECT 1 FROM staging.raw_ingest o
        WHERE o.source_system_id = r.source_system_id
          AND o.payload->>'source_record_reference' = r.payload->>'source_record_reference'
          AND o.payload->>'source_version' = r.payload->>'source_version'
          AND o.id <> r.id AND o.content_hash <> r.content_hash
          AND o.status IN ('VALID', 'PROCESSED', 'SUPERSEDED'))
    RETURNING r.id, r.error
),
dead AS (
    INSERT INTO staging.dead_letter (raw_ingest_id, error_code, error_detail)
    SELECT id, 'VERSION_CONFLICT', error FROM conflicted
    RETURNING raw_ingest_id
)
SELECT count(*) FROM dead
"""
)

_MARK_PROCESSED = text(
    """
UPDATE staging.raw_ingest r SET processed_at = now(), status = CASE WHEN EXISTS (
        SELECT 1 FROM core.incident_record i WHERE i.source_system_id = :source_id
        AND i.source_record_reference = r.canonical->>'source_record_reference'
        AND i.source_version = (r.canonical->>'source_version')::int) THEN 'PROCESSED' ELSE 'SUPERSEDED' END
WHERE r.batch_id = :batch AND r.status = 'VALID'
RETURNING r.status
"""
)

# A corrected record (same reference and version as one dead-lettered as SCHEMA_INVALID) closes
# the earlier dead letter.
_RESOLVE_DEAD_LETTERS = text(
    """
UPDATE staging.dead_letter d SET resolved_at = now(), attempts = d.attempts + 1
FROM staging.raw_ingest bad, staging.raw_ingest good
WHERE d.raw_ingest_id = bad.id AND d.resolved_at IS NULL AND d.error_code = 'SCHEMA_INVALID'
  AND good.batch_id = :batch AND good.status IN ('PROCESSED', 'SUPERSEDED')
  AND bad.source_system_id = good.source_system_id
  AND bad.payload->>'source_record_reference' = good.canonical->>'source_record_reference'
  AND bad.payload->>'source_version' = good.canonical->>'source_version'
"""
)

_LINEAGE = text(
    """
INSERT INTO core.source_record_link (source_system_id, source_record_reference, source_version,
                                     incident_id, raw_ingest_id, received_at, content_hash, lineage)
SELECT :source_id, r.canonical->>'source_record_reference', (r.canonical->>'source_version')::int,
       i.id, r.id, r.received_at, r.content_hash,
       jsonb_build_object('batch_ref', CAST(:batch_ref AS text), 'raw_status', r.status,
                          'contract', CAST(:contract AS text), 'mapping_version', CAST(:mapping AS text),
                          'beat_boundary_version', bb.version)
FROM staging.raw_ingest r
LEFT JOIN core.incident_record i ON i.source_system_id = :source_id
      AND i.source_record_reference = r.canonical->>'source_record_reference'
LEFT JOIN core.current_boundary bb ON bb.org_unit_id = i.beat_id
WHERE r.batch_id = :batch AND r.status IN ('PROCESSED', 'SUPERSEDED')
ON CONFLICT DO NOTHING
"""
)

# Rules are evaluated for the incidents the batch changed, their possible-duplicate partners (so a
# pair is found whichever record arrives first), and incidents whose open duplicate issue points at
# a changed incident (so it is re-checked, e.g. after the partner moved).
_EVALUATION_SET = text(
    """
WITH changed AS (SELECT DISTINCT unnest(CAST(:ids AS uuid[])) AS id),
partners AS (
    SELECT o.id
    FROM changed c
    JOIN core.incident_record t ON t.id = c.id
    JOIN core.incident_location tl ON tl.incident_id = t.id AND tl.geometry IS NOT NULL
    JOIN core.incident_location ol ON ST_DWithin(ol.geometry, tl.geometry, 0.001)
    JOIN core.incident_record o ON o.id = ol.incident_id
    WHERE o.id <> t.id AND o.source_system_id = t.source_system_id
      AND o.analytical_category_id = t.analytical_category_id
),
referencing AS (
    SELECT q.incident_id AS id FROM core.data_quality_issue q
    JOIN changed c ON q.detail->>'duplicate_of_id' = c.id::text
    WHERE q.rule_code = 'DQ-DUP-001' AND q.state IN ('OPEN', 'ASSIGNED')
)
SELECT id FROM changed UNION SELECT id FROM partners UNION SELECT id FROM referencing
"""
)

# Each rule returns (incident id, issue type, rule code, severity, detail). Details hold facts and
# internal ids only (never another record's reference): the API resolves a duplicate partner's
# reference only if the reader may see that record.
_RULES = text(
    """
WITH t AS (
    SELECT i.*, l.geometry AS geom, l.confidence_score AS conf
    FROM core.incident_record i LEFT JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.id = ANY(:ids)
)
SELECT id, 'MISSING_LOCATION', 'DQ-LOC-001', 'HIGH', '{}'::jsonb FROM t WHERE geom IS NULL
UNION ALL
SELECT id, 'LOW_CONFIDENCE_LOCATION', 'DQ-LOC-002', 'MEDIUM', jsonb_build_object('confidence', conf)
FROM t WHERE geom IS NOT NULL AND conf < 0.4
UNION ALL
SELECT id, 'OUTSIDE_JURISDICTION', 'DQ-LOC-003', 'HIGH', '{}'::jsonb
FROM t WHERE geom IS NOT NULL AND beat_id IS NULL
UNION ALL
SELECT id, 'MISSING_OCCURRENCE_TIME', 'DQ-TIME-001', 'MEDIUM', '{}'::jsonb
FROM t WHERE occurrence_start_at IS NULL
UNION ALL
SELECT id, 'INVALID_CATEGORY_MAPPING', 'DQ-CAT-001', 'HIGH', jsonb_build_object('code', official_offence_section)
FROM t WHERE analytical_category_id IS NULL
UNION ALL
SELECT id, 'OTHER', 'DQ-REF-001', 'HIGH', jsonb_build_object('reason', 'unknown reporting station')
FROM t WHERE reporting_station_id IS NULL
UNION ALL
SELECT t.id, 'POSSIBLE_DUPLICATE', 'DQ-DUP-001', 'MEDIUM',
       jsonb_build_object('duplicate_of_id', d.id, 'distance_m', d.distance_m)
FROM t
JOIN LATERAL (
    SELECT o.id,
           round(ST_Distance(ol.geometry::geography, t.geom::geography)::numeric, 1) AS distance_m
    FROM core.incident_location ol
    JOIN core.incident_record o ON o.id = ol.incident_id
    WHERE ST_DWithin(ol.geometry, t.geom, 0.001)                 -- index prefilter (~100 m)
      AND ST_DWithin(ol.geometry::geography, t.geom::geography, 50)
      AND o.id <> t.id AND o.source_system_id = t.source_system_id
      AND o.analytical_category_id = t.analytical_category_id
      AND abs(extract(epoch FROM o.occurrence_start_at - t.occurrence_start_at)) <= 1800
      AND (o.reported_at, o.source_record_reference) < (t.reported_at, t.source_record_reference)
    ORDER BY o.reported_at LIMIT 1
) d ON true
WHERE t.geom IS NOT NULL AND t.occurrence_start_at IS NOT NULL
"""
)

# New issues are inserted; an existing one gets the current detail. If a newer source version still
# fails a rule a reviewer had marked RESOLVED, it is reopened (and unassigned); ACCEPTED_AS_IS is a
# deliberate decision and stays as it is.
_OPEN_ISSUES = text(
    """
WITH upserted AS (
INSERT INTO core.data_quality_issue (incident_id, subject_type, subject_id, issue_type, severity,
                                     detected_by, rule_code, detail, region_id, station_id, due_at)
SELECT f.id, 'INCIDENT', f.id, f.issue_type, f.severity, 'RULE', f.rule_code, f.detail::jsonb,
       i.region_id, i.station_id, now() + interval '7 days'
FROM unnest(CAST(:ids AS uuid[]), CAST(:types AS text[]), CAST(:codes AS text[]),
            CAST(:severities AS text[]), CAST(:details AS text[]))
     AS f(id, issue_type, rule_code, severity, detail)
JOIN core.incident_record i ON i.id = f.id
ON CONFLICT (subject_type, subject_id, rule_code) DO UPDATE SET
    detail = EXCLUDED.detail, updated_at = now(),
    assigned_to = CASE WHEN core.data_quality_issue.state = 'RESOLVED' THEN NULL
                       ELSE core.data_quality_issue.assigned_to END,
    resolution_note = CASE WHEN core.data_quality_issue.state = 'RESOLVED'
                           THEN 'Reopened automatically: source version still fails this rule'
                           ELSE core.data_quality_issue.resolution_note END,
    state = CASE WHEN core.data_quality_issue.state = 'RESOLVED' THEN 'OPEN'
                 ELSE core.data_quality_issue.state END
RETURNING (xmax = 0) AS inserted
)
SELECT count(*) FILTER (WHERE inserted) FROM upserted
"""
)

# Issues follow their incident's territory (a newer version can move it to another station/region).
_RETERRITORY = text(
    """
UPDATE core.data_quality_issue q SET region_id = i.region_id, station_id = i.station_id, updated_at = now()
FROM core.incident_record i
WHERE q.incident_id = i.id AND i.id = ANY(:ids)
  AND (q.region_id IS DISTINCT FROM i.region_id OR q.station_id IS DISTINCT FROM i.station_id)
"""
)

_AUTO_RESOLVE = text(
    """
UPDATE core.data_quality_issue q
SET state = 'RESOLVED', updated_at = now(),
    resolution_note = 'Resolved automatically: corrected by source version ' || i.source_version
FROM core.incident_record i
WHERE q.incident_id = i.id AND i.id = ANY(:ids) AND q.detected_by = 'RULE'
  AND q.state IN ('OPEN', 'ASSIGNED')
  AND NOT ((q.incident_id::text || '|' || q.rule_code) = ANY(:failing))
"""
)

_QUALITY_STATUS = text(
    """
UPDATE core.incident_record i SET record_quality_status = CASE
    WHEN EXISTS (SELECT 1 FROM core.data_quality_issue q WHERE q.incident_id = i.id
                 AND q.state IN ('OPEN', 'ASSIGNED') AND q.severity = 'HIGH') THEN 'FAILED'
    WHEN EXISTS (SELECT 1 FROM core.data_quality_issue q WHERE q.incident_id = i.id
                 AND q.state IN ('OPEN', 'ASSIGNED')) THEN 'WARNING'
    ELSE 'OK' END
WHERE i.id = ANY(:ids)
"""
)


async def _run_rules(conn: AsyncConnection, changed: list[Any]) -> int:
    if not changed:
        return 0
    ids = [row[0] for row in await conn.execute(_EVALUATION_SET, {"ids": changed})]
    await conn.execute(_RETERRITORY, {"ids": ids})
    findings = (await conn.execute(_RULES, {"ids": ids})).all()
    opened = 0
    if findings:
        opened = (
            await conn.execute(
                _OPEN_ISSUES,
                {
                    "ids": [f[0] for f in findings],
                    "types": [f[1] for f in findings],
                    "codes": [f[2] for f in findings],
                    "severities": [f[3] for f in findings],
                    "details": [json.dumps(f[4], default=str) for f in findings],
                },
            )
        ).scalar_one()
    await conn.execute(_AUTO_RESOLVE, {"ids": ids, "failing": [f"{f[0]}|{f[2]}" for f in findings]})
    await conn.execute(_QUALITY_STATUS, {"ids": ids})
    return int(opened)


async def _audit_refusal(engine: AsyncEngine, code: str, reason: str, environment: str) -> None:
    await write_independent(
        engine,
        AuditEvent(
            action="INGEST_REJECTED",
            result="DENY",
            reason=reason,
            object_type="source_system",
            object_id=code,
        ),
        AuditContext(request_id=f"ingest:refused:{code}", environment=environment),
    )


async def ingest_batch_file(
    engine: AsyncEngine,
    path: Path,
    key: bytes,
    *,
    environment: str,
    expected_source: str | None = None,
) -> IngestResult:
    """Ingest one signed batch file. Idempotent at batch and record level.

    ``expected_source`` is the source of the adapter that delivered the file: a batch that declares
    a different source is rejected, so one feed's channel cannot be used to load another's data.
    Raises PermissionError when the source may not be ingested (audited); any other error rolls the
    batch back, records it as FAILED (audited) and propagates."""
    try:
        batch = await asyncio.to_thread(read_batch, path, key)
    except BatchSignatureError as exc:
        return await _reject(engine, path, str(exc), environment)
    env = batch.envelope
    source_code, batch_ref = str(env.get("source_system")), str(env.get("batch_ref"))
    if expected_source is not None and source_code != expected_source:
        return await _reject(
            engine,
            path,
            f"batch declares {source_code} but was delivered by the {expected_source} adapter",
            environment,
            code="SOURCE_MISMATCH",
            status="REJECTED_SOURCE",
        )
    records: list[dict[str, Any]] = [
        r if isinstance(r, dict) else {"_invalid": r} for r in env["records"]
    ]
    batch_hash = hashlib.sha256(canonical_json(env)).hexdigest()

    async with engine.connect() as conn:
        source = (
            await conn.execute(
                text(
                    "SELECT id, approved_status, sync_policy FROM core.source_system WHERE code = :c"
                ),
                {"c": source_code},
            )
        ).one_or_none()
    if source is None:
        raise ValueError(f"unknown source system {source_code!r}")
    if source.approved_status not in RUNNABLE_STATUSES:
        await _audit_refusal(engine, source_code, "NOT_APPROVED", environment)
        raise PermissionError(f"source {source_code} is not approved for ingestion")
    if environment == "PRODUCTION" and source.approved_status == "DEMO":
        await _audit_refusal(engine, source_code, "DEMO_IN_PRODUCTION", environment)
        raise PermissionError("PRODUCTION refuses demo/synthetic sources")
    if source.approved_status != "DEMO":
        # The only key this deployment has is the demo signing key. An approved source must get its
        # own key (per-source key management is not built), so the demo key can never load it.
        await _audit_refusal(engine, source_code, "NO_SOURCE_KEY", environment)
        raise PermissionError(f"no signing key is configured for approved source {source_code}")
    mapping = (source.sync_policy or {}).get("mapping_version", "")
    try:
        extracted_at: datetime | None = datetime.fromisoformat(str(env.get("extracted_at")))
    except ValueError:
        extracted_at = None
    if extracted_at is not None and extracted_at.tzinfo is None:
        extracted_at = None  # naive timestamps are ambiguous; freshness falls back to receipt time

    try:
        result = await _process(
            engine,
            source=source,
            source_code=source_code,
            batch_ref=batch_ref,
            batch_hash=batch_hash,
            extracted_at=extracted_at,
            records=records,
            mapping=mapping,
            environment=environment,
        )
    except Exception as error:
        await _record_failure(
            engine,
            source_code=source_code,
            batch_ref=batch_ref,
            content_hash=batch_hash,
            record_count=len(records),
            error=error,
            environment=environment,
        )
        raise
    if result.status == "REJECTED_BATCH_REF":
        await _reject(
            engine,
            path,
            f"batch reference {batch_ref} was already used for different content",
            environment,
            code="BATCH_REF_CONFLICT",
            status="REJECTED_BATCH_REF",
        )
    return result


async def _process(
    engine: AsyncEngine,
    *,
    source: Any,
    source_code: str,
    batch_ref: str,
    batch_hash: str,
    extracted_at: datetime | None,
    records: list[dict[str, Any]],
    mapping: str,
    environment: str,
) -> IngestResult:
    async with engine.begin() as conn:
        # One batch at a time per source: concurrent batches would not see each other's records
        # (duplicate rule, version conflicts).
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('crimemap-ingest:' || :c))"),
            {"c": source_code},
        )
        # A FAILED batch may be re-delivered and processed; anything else under the same reference
        # is either the same content (already processed) or a conflict.
        batch_id = (
            await conn.execute(
                text(
                    "INSERT INTO staging.ingest_batch (source_system_id, batch_ref, source_extracted_at, "
                    "signature_valid, record_count, content_sha256) VALUES (:s, :b, "
                    "least(coalesce(CAST(:x AS timestamptz), now()), now() + interval '5 minutes'), true, :n, :h) "
                    "ON CONFLICT (source_system_id, batch_ref) DO UPDATE SET status = 'RECEIVED', "
                    "received_at = now(), source_extracted_at = EXCLUDED.source_extracted_at, "
                    "record_count = EXCLUDED.record_count, content_sha256 = EXCLUDED.content_sha256, "
                    "accepted = 0, duplicates = 0, rejected = 0, completed_at = NULL "
                    "WHERE staging.ingest_batch.status = 'FAILED' RETURNING id"
                ),
                {
                    "s": source.id,
                    "b": batch_ref,
                    "x": extracted_at,
                    "n": len(records),
                    "h": batch_hash,
                },
            )
        ).scalar_one_or_none()
        if batch_id is None:
            existing = (
                await conn.execute(
                    text(
                        "SELECT content_sha256 FROM staging.ingest_batch "
                        "WHERE source_system_id = :s AND batch_ref = :b"
                    ),
                    {"s": source.id, "b": batch_ref},
                )
            ).scalar_one()
            status = (
                "REJECTED_BATCH_REF"
                if existing is not None and existing != batch_hash
                else "ALREADY_PROCESSED"
            )
            return IngestResult(batch_ref, source_code, status, len(records))

        # 1. Stage raw records exactly as received.
        hashes = [hashlib.sha256(canonical_json(r)).hexdigest() for r in records]
        keys = [idempotency_key(source_code, r, h) for r, h in zip(records, hashes, strict=True)]
        payloads = [canonical_json(r).decode() for r in records]
        inserted = {
            row[0]
            for row in await conn.execute(
                text(
                    "INSERT INTO staging.raw_ingest (batch_id, source_system_id, payload, content_hash, idempotency_key) "
                    "SELECT :b, :s, CAST(p AS jsonb), h, k FROM unnest(CAST(:p AS text[]), CAST(:h AS text[]), "
                    "CAST(:k AS text[])) AS t(p, h, k) ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key"
                ),
                {"b": batch_id, "s": source.id, "p": payloads, "h": hashes, "k": keys},
            )
        }
        duplicates = len(records) - len(inserted)

        # 2. Validate against the canonical contract; keep the normalised form for processing.
        valid, canonical, invalid, details = [], [], [], []
        dropped = 0
        seen: set[str] = set()
        for record, k in zip(records, keys, strict=True):
            if k not in inserted or k in seen:
                continue
            seen.add(k)
            try:
                parsed = SourceRecord.model_validate(record)
                if parsed.source_system != source_code:
                    raise ValueError("source_system does not match the batch")
            except ValidationError as exc:
                invalid.append(k)
                details.append(_error_summary(exc))
                continue
            except ValueError as exc:
                invalid.append(k)
                details.append(str(exc))
                continue
            valid.append(k)
            canonical.append(json.dumps(parsed.model_dump(mode="json")))
            dropped += len(parsed.dropped_fields)
        if valid:
            await conn.execute(
                text(
                    "UPDATE staging.raw_ingest r SET status = 'VALID', canonical = CAST(c.doc AS jsonb) "
                    "FROM unnest(CAST(:k AS text[]), CAST(:c AS text[])) AS c(k, doc) "
                    "WHERE r.batch_id = :b AND r.idempotency_key = c.k"
                ),
                {"b": batch_id, "k": valid, "c": canonical},
            )
        if invalid:
            await conn.execute(
                text(
                    "WITH bad AS (SELECT * FROM unnest(CAST(:k AS text[]), CAST(:d AS text[])) AS t(k, d)), "
                    "upd AS (UPDATE staging.raw_ingest r SET status = 'INVALID', error = bad.d, processed_at = now() "
                    "FROM bad WHERE r.batch_id = :b AND r.idempotency_key = bad.k RETURNING r.id, bad.d) "
                    "INSERT INTO staging.dead_letter (raw_ingest_id, error_code, error_detail) "
                    "SELECT id, 'SCHEMA_INVALID', d FROM upd"
                ),
                {"b": batch_id, "k": invalid, "d": details},
            )
        conflicts = int((await conn.execute(_VERSION_CONFLICTS, {"batch": batch_id})).scalar_one())

        # 3. Mapping, spatial enrichment, storage, lineage.
        params = {
            "batch": batch_id,
            "source_id": source.id,
            "mapping": mapping,
            "is_demo": source.approved_status == "DEMO",
        }
        ids = [row[0] for row in await conn.execute(_UPSERT_INCIDENTS, params)]
        await conn.execute(_UPSERT_LOCATIONS, {**params, "ids": ids})
        await conn.execute(_UPDATE_RESTRICTED, {**params, "ids": ids})
        await conn.execute(_INSERT_RESTRICTED, {**params, "ids": ids})
        statuses = [row[0] for row in await conn.execute(_MARK_PROCESSED, params)]
        await conn.execute(_RESOLVE_DEAD_LETTERS, params)
        await conn.execute(_LINEAGE, {**params, "batch_ref": batch_ref, "contract": CONTRACT})

        # 4. Data-quality rules.
        issues = await _run_rules(conn, ids)

        # 5. Statistics and audit.
        accepted = len(valid) - conflicts
        result = IngestResult(
            batch_ref,
            source_code,
            "PROCESSED",
            len(records),
            accepted=accepted,
            duplicates=duplicates,
            rejected=len(invalid) + conflicts,
            superseded=statuses.count("SUPERSEDED"),
            issues_opened=issues,
        )
        await conn.execute(
            text(
                "UPDATE staging.ingest_batch SET accepted = :a, duplicates = :d, rejected = :r, "
                "status = 'PROCESSED', completed_at = now() WHERE id = :b"
            ),
            {"a": result.accepted, "d": duplicates, "r": result.rejected, "b": batch_id},
        )
        await conn.execute(
            text(
                "UPDATE core.source_system SET last_batch_at = greatest(last_batch_at, now()) WHERE id = :s"
            ),
            {"s": source.id},
        )
        await insert_event(
            conn,
            AuditEvent(
                action="INGEST_BATCH",
                result="ALLOW",
                object_type="ingest_batch",
                object_id=str(batch_id),
                after_meta={
                    "source": source_code,
                    "batch_ref": batch_ref,
                    "records": len(records),
                    "accepted": result.accepted,
                    "duplicates": duplicates,
                    "rejected": result.rejected,
                    "version_conflicts": conflicts,
                    "superseded": result.superseded,
                    "incidents_changed": len(ids),
                    "issues_opened": issues,
                    "dropped_unknown_fields": dropped,
                },
            ),
            AuditContext(request_id=f"ingest:{batch_ref}", environment=environment),
        )
    log.info("ingest_batch_processed", extra={"batch_ref": batch_ref, "accepted": result.accepted})
    return result


async def check_source_health(engine: AsyncEngine, *, environment: str) -> dict[str, str]:
    """Mark each active source OK / STALE / FAILING / NEVER_RUN and open (or auto-resolve) a
    STALE_SOURCE data-quality issue. Freshness is the source's extraction time (never later than
    receipt; receipt time when the source gave none). FAILING means the latest verified batch
    failed processing; signature rejections are counted in reconciliation but cannot flip health,
    so nobody can change a feed's status by dropping junk files."""
    statuses: dict[str, str] = {}
    async with engine.begin() as conn:
        sources = (
            await conn.execute(
                text(
                    "SELECT s.id, s.code, coalesce((s.sync_policy->>'stale_after_hours')::int, 48) AS hours, "
                    "(SELECT max(least(coalesce(b.source_extracted_at, b.received_at), b.received_at)) "
                    " FROM staging.ingest_batch b WHERE b.source_system_id = s.id AND b.status = 'PROCESSED') AS extracted, "
                    "(SELECT b.status FROM staging.ingest_batch b WHERE b.source_system_id = s.id "
                    " AND b.signature_valid ORDER BY b.received_at DESC LIMIT 1) AS last_status "
                    "FROM core.source_system s WHERE s.approved_status IN ('DEMO', 'APPROVED')"
                )
            )
        ).all()
        now = datetime.now(UTC)
        for s in sources:
            stale = s.extracted is not None and (now - s.extracted).total_seconds() > s.hours * 3600
            if s.last_status == "FAILED":
                status = "FAILING"
            elif s.extracted is None:
                status = "NEVER_RUN"
            else:
                status = "STALE" if stale else "OK"
            statuses[s.code] = status
            await conn.execute(
                text(
                    "UPDATE core.source_system SET last_health_status = :st, last_health_check_at = now() WHERE id = :id"
                ),
                {"st": status, "id": s.id},
            )
            if stale:
                await conn.execute(
                    text(
                        "INSERT INTO core.data_quality_issue (subject_type, subject_id, issue_type, severity, "
                        "detected_by, rule_code, detail) VALUES ('SOURCE_SYSTEM', :id, 'STALE_SOURCE', 'HIGH', "
                        "'RULE', 'DQ-SRC-001', jsonb_build_object('last_extracted_at', CAST(:x AS text), "
                        "'stale_after_hours', CAST(:h AS int))) "
                        "ON CONFLICT (subject_type, subject_id, rule_code) DO UPDATE SET "
                        "state = CASE WHEN core.data_quality_issue.state = 'RESOLVED' THEN 'OPEN' "
                        "ELSE core.data_quality_issue.state END, detail = EXCLUDED.detail, updated_at = now()"
                    ),
                    {"id": s.id, "x": s.extracted.isoformat(), "h": s.hours},
                )
            elif s.extracted is not None:
                await conn.execute(
                    text(
                        "UPDATE core.data_quality_issue SET state = 'RESOLVED', updated_at = now(), "
                        "resolution_note = 'Resolved automatically: source is fresh again' "
                        "WHERE subject_type = 'SOURCE_SYSTEM' AND subject_id = :id AND rule_code = 'DQ-SRC-001' "
                        "AND state IN ('OPEN', 'ASSIGNED')"
                    ),
                    {"id": s.id},
                )
        await insert_event(
            conn,
            AuditEvent(
                action="SOURCE_HEALTH_CHECK",
                result="ALLOW",
                object_type="source_system",
                after_meta={"statuses": statuses},
            ),
            AuditContext(request_id=f"health:{now.isoformat()}", environment=environment),
        )
    return statuses


async def _ingest_one(
    engine: AsyncEngine, path: Path, key: bytes, *, environment: str, expected_source: str | None
) -> IngestResult:
    """Ingest a file; a batch that can never succeed as delivered becomes a FAILED result (already
    recorded and audited) instead of stopping the run. Transient errors propagate for retry."""
    try:
        return await ingest_batch_file(
            engine, path, key, environment=environment, expected_source=expected_source
        )
    except Exception as error:
        if is_transient(error):
            raise
        log.error("ingest_file_failed", extra={"file": path.name, "error": type(error).__name__})
        return IngestResult(path.stem, expected_source or "?", "FAILED")


async def ingest_directory(
    engine: AsyncEngine, directory: Path, key: bytes, *, environment: str
) -> list[IngestResult]:
    """Ingest every batch file in a directory, in name order, then refresh source health."""
    # Regular files only, as in the scheduled inbox: a link could point at a file elsewhere.
    paths = sorted(
        await asyncio.to_thread(
            lambda: [p for p in directory.glob("*.json") if p.is_file() and not p.is_symlink()]
        )
    )
    results: list[IngestResult] = []
    try:
        for path in paths:
            if not path.name.endswith("-manifest.json"):
                results.append(
                    await _ingest_one(
                        engine, path, key, environment=environment, expected_source=None
                    )
                )
    finally:
        await check_source_health(engine, environment=environment)
    return results


def _file_away(path: Path, folder: str) -> None:
    target = path.parent / folder
    target.mkdir(exist_ok=True)
    shutil.move(str(path), target / path.name)


async def ingest_inbox(
    engine: AsyncEngine, inbox: Path, key: bytes, *, environment: str
) -> dict[str, int]:
    """Scheduled ingestion: ask each runnable source's adapter for its pending batches, ingest
    them, and file each away under ``processed/``, ``rejected/`` or ``failed/``. A transient error
    (database down, etc.) propagates so the job is retried with exponential backoff; the file stays
    in the inbox and, because ingestion is idempotent, is safely re-processed."""
    async with engine.connect() as conn:
        sources = (
            await conn.execute(
                text("SELECT code, approved_status FROM core.source_system ORDER BY code")
            )
        ).all()
    summary: Counter[str] = Counter()
    try:
        for source in sources:
            if source.approved_status not in RUNNABLE_STATUSES:
                continue  # government stubs: never built, never run
            if environment == "PRODUCTION" and source.approved_status == "DEMO":
                continue
            adapter = adapter_for(source.code, source.approved_status, inbox)
            for path in await asyncio.to_thread(adapter.pending_batches):
                result = await _ingest_one(
                    engine, path, key, environment=environment, expected_source=source.code
                )
                summary[result.status] += 1
                folder = (
                    "rejected"
                    if result.status.startswith("REJECTED")
                    else "failed"
                    if result.status == "FAILED"
                    else "processed"
                )
                await asyncio.to_thread(_file_away, path, folder)
    finally:
        await check_source_health(engine, environment=environment)
    return dict(summary)


_RECONCILE = text(
    """
SELECT s.id AS source_system_id, r.source_code, r.batches, r.declared, r.accepted, r.duplicates,
       r.rejected, r.unexplained, r.rejected_batches
FROM core.ingest_reconciliation r JOIN core.source_system s ON s.code = r.source_code
WHERE r.received_on = :day
ORDER BY r.source_code
"""
)

_RECONCILE_ISSUE = text(
    """
INSERT INTO core.data_quality_issue (subject_type, subject_id, issue_type, severity, detected_by,
                                     rule_code, detail)
VALUES ('SOURCE_SYSTEM', :source_id, 'OTHER', 'HIGH', 'RULE', 'DQ-REC-001',
        jsonb_build_object('reason', 'reconciliation does not balance', 'day', CAST(:day AS text),
                           'unexplained', CAST(:unexplained AS bigint)))
ON CONFLICT (subject_type, subject_id, rule_code) DO UPDATE SET
    state = CASE WHEN core.data_quality_issue.state = 'RESOLVED' THEN 'OPEN'
                 ELSE core.data_quality_issue.state END,
    detail = EXCLUDED.detail, updated_at = now()
"""
)

_RECONCILE_RESOLVE = text(
    """
UPDATE core.data_quality_issue SET state = 'RESOLVED', updated_at = now(),
    resolution_note = 'Resolved automatically: reconciliation balances for ' || CAST(:day AS text)
WHERE subject_type = 'SOURCE_SYSTEM' AND subject_id = :source_id AND rule_code = 'DQ-REC-001'
  AND state IN ('OPEN', 'ASSIGNED') AND detail->>'day' = CAST(:day AS text)
"""
)


async def reconcile_day(
    conn: AsyncConnection, day: date, *, environment: str
) -> list[dict[str, Any]]:
    """Daily reconciliation report (spec: daily reconciliation). For every source that delivered
    batches on ``day`` (UTC): declared = accepted + duplicates + rejected must hold. A source that
    does not balance gets an open DQ-REC-001 issue for the steward (reopened if it had been
    resolved; ACCEPTED_AS_IS is respected); a later balanced run of the same day resolves it. The
    report is audited."""
    rows = [dict(r._mapping) for r in await conn.execute(_RECONCILE, {"day": day})]
    for row in rows:
        params = {"source_id": row["source_system_id"], "day": day.isoformat()}
        if row["unexplained"] != 0:
            await conn.execute(_RECONCILE_ISSUE, {**params, "unexplained": row["unexplained"]})
        else:
            await conn.execute(_RECONCILE_RESOLVE, params)
    totals = {
        k: int(sum(r[k] for r in rows))
        for k in (
            "declared",
            "accepted",
            "duplicates",
            "rejected",
            "unexplained",
            "rejected_batches",
        )
    }
    await insert_event(
        conn,
        AuditEvent(
            action="RECONCILIATION_REPORT",
            result="ALLOW",
            object_type="ingest_reconciliation",
            object_id=day.isoformat(),
            after_meta={"day": day.isoformat(), "sources": len(rows), **totals},
        ),
        AuditContext(request_id=f"reconcile:{day.isoformat()}", environment=environment),
    )
    for row in rows:
        row.pop("source_system_id")
    return rows
