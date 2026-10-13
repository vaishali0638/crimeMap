"""Phase 5 (Analytics Workbench): analytics selection, evidence snapshots, pattern reviews and
controlled report exports.

- core.map_selection also returns each record's occurrence and report times (the occurrence-to-
  report delay view); arguments unchanged, so every map and dashboard query is unaffected.
- core.analytics_selection: the same selection plus the workbench-only filters (locality, time of
  day in IST) and the ADR-012 "masked" flag, so every analytics statement shares one definition.
- core.evidence_snapshot: an immutable copy of an analysis result (data, meta, parameters, hash),
  the evidence a pattern review must cite.
- core.pattern_review: analyst pattern reviews (Phase 5 creates drafts; Phase 6 adds the
  supervisor decision). No person-level column exists, by design.
- core.report_export: generated CSV/PDF exports, readable only by their requester, kept 24 hours.
  They are stored in the database, not object storage, until an S3 client is added (ADR-035).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_SIGNATURE = """uuid, uuid[], uuid[], uuid[], text[], text[], timestamptz, timestamptz, text, numeric,
    boolean, uuid[], uuid[]"""

_ARGS = """p_region uuid, p_stations uuid[], p_beats uuid[], p_categories uuid[], p_record_types text[],
    p_statuses text[], p_from timestamptz, p_to timestamptz, p_basis text, p_min_confidence numeric,
    p_scope_global boolean, p_scope_regions uuid[], p_scope_stations uuid[]"""

_WHERE = """
    FROM core.incident_record i
    LEFT JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = p_region
      AND (p_scope_global OR i.region_id = ANY (p_scope_regions) OR i.station_id = ANY (p_scope_stations))
      AND (p_stations IS NULL OR i.station_id = ANY (p_stations))
      AND (p_beats IS NULL OR i.beat_id = ANY (p_beats))
      AND (p_categories IS NULL OR i.analytical_category_id = ANY (p_categories))
      AND (p_record_types IS NULL OR i.record_type = ANY (p_record_types))
      AND (p_statuses IS NULL OR i.status_code = ANY (p_statuses))
      AND CASE WHEN p_basis = 'REPORTED' THEN i.reported_at ELSE i.occurrence_start_at END >= p_from
      AND CASE WHEN p_basis = 'REPORTED' THEN i.reported_at ELSE i.occurrence_start_at END < p_to
      AND (p_min_confidence = 0 OR l.confidence_score >= p_min_confidence)
$$;"""

_COLUMNS_0007 = """id uuid, region_id uuid, station_id uuid, beat_id uuid, analytical_category_id uuid,
               is_sensitive boolean, record_type text, status_code text, quality text,
               geometry geometry, x double precision, y double precision, crs integer,
               is_residential boolean, precision_level text,
               event_at timestamptz, locality text"""

_SELECT_0007 = """i.id, i.region_id, i.station_id, i.beat_id, i.analytical_category_id,
           i.is_sensitive_category, i.record_type, i.status_code, i.record_quality_status,
           l.geometry, l.analysis_x, l.analysis_y, l.analysis_crs,
           coalesce(l.is_residential, false), l.precision_level,
           CASE WHEN p_basis = 'REPORTED' THEN i.reported_at ELSE i.occurrence_start_at END,
           l.locality"""


def upgrade() -> None:
    run_sql(
        f"""
-- 1. Selection with both times -------------------------------------------------------------------
DROP FUNCTION core.map_selection({_SIGNATURE});
CREATE FUNCTION core.map_selection({_ARGS})
RETURNS TABLE ({_COLUMNS_0007},
               occurred_at timestamptz, reported_at timestamptz,
               confidence numeric, geocode_method text)
LANGUAGE sql STABLE AS $$
    SELECT {_SELECT_0007},
           i.occurrence_start_at, i.reported_at, l.confidence_score, l.geocode_method
{_WHERE}
GRANT EXECUTE ON FUNCTION core.map_selection({_SIGNATURE}) TO crimemap_app;

-- 2. Analytics selection: + locality and IST time-of-day filters, + the ADR-012 masked flag ---------
CREATE FUNCTION core.analytics_selection(
    {_ARGS},
    p_locality text, p_hour_from integer, p_hour_to integer,
    p_sens_global boolean, p_sens_regions uuid[], p_sens_stations uuid[])
RETURNS TABLE ({_COLUMNS_0007},
               occurred_at timestamptz, reported_at timestamptz,
               confidence numeric, geocode_method text, masked boolean)
LANGUAGE sql STABLE AS $$
    SELECT s.*,
           (s.is_sensitive AND NOT (p_sens_global OR s.region_id = ANY (p_sens_regions)
                                    OR s.station_id = ANY (p_sens_stations)))
    FROM core.map_selection(p_region, p_stations, p_beats, p_categories, p_record_types, p_statuses,
                            p_from, p_to, p_basis, p_min_confidence, p_scope_global, p_scope_regions,
                            p_scope_stations) s
    WHERE (p_locality IS NULL OR s.locality ILIKE p_locality)
      AND (p_hour_from IS NULL OR (
            CASE WHEN p_hour_from <= p_hour_to
                 THEN extract(hour FROM s.event_at AT TIME ZONE 'Asia/Kolkata') BETWEEN p_hour_from AND p_hour_to
                 ELSE extract(hour FROM s.event_at AT TIME ZONE 'Asia/Kolkata') >= p_hour_from
                   OR extract(hour FROM s.event_at AT TIME ZONE 'Asia/Kolkata') <= p_hour_to END))
$$;
GRANT EXECUTE ON FUNCTION core.analytics_selection({_SIGNATURE}, text, integer, integer, boolean, uuid[],
    uuid[]) TO crimemap_app;

-- 3. Evidence snapshots (immutable) --------------------------------------------------------------
CREATE TABLE core.evidence_snapshot (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    section          text NOT NULL CHECK (section IN ('overview','location','case_trends','comparison','data_quality')),
    params           jsonb NOT NULL,
    data             jsonb NOT NULL,
    meta             jsonb NOT NULL,
    content_sha256   text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$'),
    classification   text NOT NULL CHECK (classification IN ('INTERNAL','RESTRICTED')),
    region_id        uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id       uuid REFERENCES core.organizational_unit (id),
    created_by       uuid NOT NULL REFERENCES core.app_user (id),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_snapshot_scope_idx ON core.evidence_snapshot (region_id, created_at DESC);
ALTER TABLE core.evidence_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.evidence_snapshot FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_app_scope ON core.evidence_snapshot FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids'))
       OR created_by = core.session_user_id())
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids'))
            OR (SELECT core.scope_global()));
CREATE POLICY evidence_author ON core.evidence_snapshot AS RESTRICTIVE FOR INSERT TO crimemap_app
WITH CHECK (created_by = core.session_user_id());
CREATE POLICY evidence_owner ON core.evidence_snapshot TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (section, params, data, meta, content_sha256, classification, region_id, station_id,
                      created_by) ON core.evidence_snapshot TO crimemap_app;

-- 4. Pattern reviews (Phase 5: drafts) ------------------------------------------------------------
CREATE TABLE core.pattern_review (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_type            text NOT NULL CHECK (review_type IN ('HOTSPOT','EMERGING','NEAR_REPEAT','SERIES','CIVIC_CONTEXT','DATA_QUALITY')),
    grid_cell_ids          text[] NOT NULL CHECK (cardinality(grid_cell_ids) BETWEEN 1 AND 200),
    area_geometry          geometry(MultiPolygon, 4326),
    analytical_category_id uuid REFERENCES core.analytical_category (id),
    category_label         text NOT NULL,
    period_start           date NOT NULL,
    period_end             date NOT NULL,
    baseline_start         date NOT NULL,
    baseline_end           date NOT NULL,
    record_count           integer NOT NULL CHECK (record_count >= 0),
    baseline_count         integer CHECK (baseline_count >= 0),
    quality_summary        jsonb NOT NULL,
    evidence_snapshot_id   uuid NOT NULL REFERENCES core.evidence_snapshot (id),
    analyst_narrative      text NOT NULL CHECK (length(analyst_narrative) BETWEEN 20 AND 4000),
    recommended_next_step  text NOT NULL CHECK (length(recommended_next_step) BETWEEN 5 AND 1000),
    analyst_status         text NOT NULL DEFAULT 'DRAFT' CHECK (analyst_status IN ('DRAFT','SUBMITTED')),
    supervisor_status      text CHECK (supervisor_status IN ('PENDING','APPROVED','RETURNED','REJECTED')),
    region_id              uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id             uuid REFERENCES core.organizational_unit (id),
    created_by             uuid NOT NULL REFERENCES core.app_user (id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CHECK (period_start <= period_end AND baseline_start <= baseline_end)
);
CREATE INDEX pattern_review_scope_idx ON core.pattern_review (region_id, created_at DESC);
ALTER TABLE core.pattern_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.pattern_review FORCE ROW LEVEL SECURITY;
CREATE POLICY pattern_review_app_scope ON core.pattern_review FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY pattern_review_author ON core.pattern_review AS RESTRICTIVE FOR INSERT TO crimemap_app
WITH CHECK (created_by = core.session_user_id() AND analyst_status = 'DRAFT' AND supervisor_status IS NULL);
CREATE POLICY pattern_review_owner ON core.pattern_review TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (review_type, grid_cell_ids, area_geometry, analytical_category_id, category_label,
                      period_start, period_end, baseline_start, baseline_end, record_count, baseline_count,
                      quality_summary, evidence_snapshot_id, analyst_narrative, recommended_next_step,
                      region_id, station_id, created_by) ON core.pattern_review TO crimemap_app;

-- 5. Notes on evidence snapshots ------------------------------------------------------------------
ALTER TABLE core.analyst_note DROP CONSTRAINT analyst_note_subject_type_check;
ALTER TABLE core.analyst_note ADD CONSTRAINT analyst_note_subject_type_check
    CHECK (subject_type IN ('INCIDENT','CELL','PATTERN_REVIEW','ACTION','DQ_ISSUE','EVIDENCE_SNAPSHOT'));

-- 6. Controlled exports (requester only; 24-hour expiry) ----------------------------------------------
CREATE TABLE core.report_export (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    format          text NOT NULL CHECK (format IN ('CSV','PDF')),
    section         text NOT NULL,
    filename        text NOT NULL CHECK (filename ~ '^[A-Za-z0-9_.-]{{1,120}}$'),
    content         bytea NOT NULL CHECK (octet_length(content) <= 5242880),
    content_sha256  text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$'),
    classification  text NOT NULL CHECK (classification IN ('INTERNAL','RESTRICTED')),
    metadata        jsonb NOT NULL,
    evidence_snapshot_id uuid NOT NULL REFERENCES core.evidence_snapshot (id),
    region_id       uuid NOT NULL REFERENCES core.organizational_unit (id),
    created_by      uuid NOT NULL REFERENCES core.app_user (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    downloads       integer NOT NULL DEFAULT 0,
    CHECK (expires_at > created_at AND expires_at <= created_at + interval '24 hours')
);
CREATE INDEX report_export_expiry_idx ON core.report_export (expires_at);
ALTER TABLE core.report_export ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.report_export FORCE ROW LEVEL SECURITY;
CREATE POLICY report_export_requester ON core.report_export FOR ALL TO crimemap_app
USING (created_by = core.session_user_id()) WITH CHECK (created_by = core.session_user_id());
CREATE POLICY report_export_worker ON core.report_export TO crimemap_worker USING (true) WITH CHECK (true);
CREATE POLICY report_export_owner ON core.report_export TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (format, section, filename, content, content_sha256, classification, metadata,
                      evidence_snapshot_id, region_id, created_by, expires_at) ON core.report_export TO crimemap_app;
GRANT UPDATE (downloads) ON core.report_export TO crimemap_app;
GRANT SELECT (id, expires_at), DELETE ON core.report_export TO crimemap_worker;
"""
    )


def downgrade() -> None:
    run_sql(
        f"""
DROP TABLE core.report_export;
DELETE FROM core.analyst_note WHERE subject_type = 'EVIDENCE_SNAPSHOT';
ALTER TABLE core.analyst_note DROP CONSTRAINT analyst_note_subject_type_check;
ALTER TABLE core.analyst_note ADD CONSTRAINT analyst_note_subject_type_check
    CHECK (subject_type IN ('INCIDENT','CELL','PATTERN_REVIEW','ACTION','DQ_ISSUE'));
DROP TABLE core.pattern_review;
DROP TABLE core.evidence_snapshot;
DROP FUNCTION core.analytics_selection({_SIGNATURE}, text, integer, integer, boolean, uuid[], uuid[]);
DROP FUNCTION core.map_selection({_SIGNATURE});
CREATE FUNCTION core.map_selection({_ARGS})
RETURNS TABLE ({_COLUMNS_0007})
LANGUAGE sql STABLE AS $$
    SELECT {_SELECT_0007}
{_WHERE}
GRANT EXECUTE ON FUNCTION core.map_selection({_SIGNATURE}) TO crimemap_app;
"""
    )
