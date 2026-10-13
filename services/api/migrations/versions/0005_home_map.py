"""Phase 3 (Home Map): metric coordinates for fast map aggregation, operational records, analyst
notes, user-flagged data-quality issues, and search indexes.

- incident_location gets the point in its region's analysis CRS (metres), kept current by a
  trigger, so grid/hexagon binning is plain arithmetic (ADR-005, ADR-032). Measured on the 200k demo
  set: hexagon join with ST_HexagonGrid 9.6 s, on-the-fly transform + binning 0.9 s, stored metres
  well under the 500 ms target (ADR-017).
- core.operational_record: the six "+ Add Operational Record" kinds. There is no FIR/case kind
  and no path from this table to core.incident_record (spec: "No create FIR option").
- core.analyst_note: append-only notes on incidents, cells, issues.
- The API role may insert user-flagged DQ issues (detected_by = USER) in its scope.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
-- 1. Metric coordinates and location scope ---------------------------------------------------------
CREATE FUNCTION core.scope_global() RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.scope_global', true), '') = 'on'
$$;
GRANT EXECUTE ON FUNCTION core.scope_global() TO crimemap_app;
ALTER TABLE core.incident_location
    ADD COLUMN analysis_crs integer CHECK (analysis_crs IN (32643, 32644)),
    ADD COLUMN analysis_x double precision,
    ADD COLUMN analysis_y double precision,
    -- Copies of the incident's scope columns, so the location's RLS policy is a direct predicate
    -- rather than a per-row subquery into incident_record (Phase 3 load test: 858k buffer hits for
    -- 107k rows). Kept in sync by the triggers below; never written directly.
    ADD COLUMN region_id uuid,
    ADD COLUMN station_id uuid,
    ADD COLUMN access_classification text;

-- Before every insert/update of a location: copy the incident's scope and compute metres in the
-- region's analysis CRS.
CREATE FUNCTION core.sync_incident_location() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, core AS $$
DECLARE
    v_crs integer;
    v_p geometry;
BEGIN
    SELECT i.region_id, i.station_id, i.access_classification, r.analysis_crs
      INTO NEW.region_id, NEW.station_id, NEW.access_classification, v_crs
    FROM core.incident_record i LEFT JOIN core.organizational_unit r ON r.id = i.region_id
    WHERE i.id = NEW.incident_id;
    IF NEW.geometry IS NULL THEN
        NEW.analysis_crs := NULL; NEW.analysis_x := NULL; NEW.analysis_y := NULL;
        RETURN NEW;
    END IF;
    IF v_crs IS NULL THEN
        -- unknown region: Mahe lies west of 78 degrees E (UTM 43N), the other regions in 44N
        v_crs := CASE WHEN ST_X(NEW.geometry) < 78 THEN 32643 ELSE 32644 END;
    END IF;
    v_p := ST_Transform(NEW.geometry, v_crs);
    NEW.analysis_crs := v_crs; NEW.analysis_x := ST_X(v_p); NEW.analysis_y := ST_Y(v_p);
    RETURN NEW;
END $$;
CREATE TRIGGER incident_location_sync BEFORE INSERT OR UPDATE ON core.incident_location
FOR EACH ROW EXECUTE FUNCTION core.sync_incident_location();

-- When a newer source version moves or reclassifies an incident, its location follows.
CREATE FUNCTION core.propagate_incident_scope() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
BEGIN
    UPDATE core.incident_location SET incident_id = incident_id WHERE incident_id = NEW.id;
    RETURN NULL;
END $$;
CREATE TRIGGER incident_record_scope_propagation
AFTER UPDATE OF region_id, station_id, access_classification ON core.incident_record
FOR EACH ROW WHEN (OLD.region_id IS DISTINCT FROM NEW.region_id OR OLD.station_id IS DISTINCT FROM NEW.station_id
                   OR OLD.access_classification IS DISTINCT FROM NEW.access_classification)
EXECUTE FUNCTION core.propagate_incident_scope();

-- Backfill (fires the trigger). The rewrite leaves every row version dead, so refresh planner
-- statistics now (VACUUM cannot run inside the migration transaction; autovacuum reclaims the space,
-- and `VACUUM ANALYZE core.incident_location` after migrating a large database speeds the first queries).
UPDATE core.incident_location SET incident_id = incident_id;
ANALYZE core.incident_location;
ANALYZE core.incident_record;

DROP POLICY incident_location_app_scope ON core.incident_location;
-- Scope lookups are scalar subqueries so Postgres evaluates them once per query (InitPlan), not per
-- row: on 107k rows this took the scan from 196 ms to about 80 ms (Phase 3 load test).
CREATE POLICY incident_location_app_scope ON core.incident_location FOR SELECT TO crimemap_app
USING (
    access_classification <> 'HIGHLY_RESTRICTED'
    AND ((SELECT core.scope_global())
         OR region_id = ANY (CAST((SELECT core.scope_ids('app.region_ids')) AS uuid[]))
         OR station_id = ANY (CAST((SELECT core.scope_ids('app.station_ids')) AS uuid[])))
);

CREATE INDEX incident_region_occurrence_idx ON core.incident_record (region_id, occurrence_start_at);
CREATE INDEX incident_station_occurrence_idx ON core.incident_record (station_id, occurrence_start_at);
CREATE INDEX incident_reference_prefix_idx ON core.incident_record (source_record_reference text_pattern_ops);

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;
CREATE INDEX incident_location_locality_trgm_idx ON core.incident_location USING gin (locality gin_trgm_ops);

-- Hexagon helpers (pointy-top axial coordinates; s = circumradius in metres).
CREATE FUNCTION core.hex_cell(x double precision, y double precision, s double precision)
RETURNS integer[] LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN ARRAY[(-ry - rr)::int, rr::int]
        WHEN abs(ry - cy) > abs(rr - r) THEN ARRAY[rq::int, rr::int]
        ELSE ARRAY[rq::int, (-rq - ry)::int] END
    FROM (SELECT q, r, -q - r AS cy, round(q) AS rq, round(r) AS rr, round(-q - r) AS ry
          FROM (SELECT (sqrt(3) / 3 * x - y / 3) / s AS q, (2.0 / 3 * y) / s AS r) f) c
$$;
CREATE FUNCTION core.hex_polygon(q integer, r integer, s double precision, crs integer)
RETURNS geometry LANGUAGE sql IMMUTABLE PARALLEL SAFE SET search_path = pg_catalog, public AS $$
    SELECT ST_Transform(ST_SetSRID(ST_MakePolygon(ST_MakeLine(ARRAY(
        SELECT ST_MakePoint(cx + s * cos(radians(30 + 60 * k)), cy + s * sin(radians(30 + 60 * k)))
        FROM generate_series(0, 6) k))), crs), 4326)
    FROM (SELECT s * sqrt(3) * (q + r / 2.0) AS cx, s * 1.5 * r AS cy) c
$$;
GRANT EXECUTE ON FUNCTION core.hex_cell(double precision, double precision, double precision),
                          core.hex_polygon(integer, integer, double precision, integer) TO crimemap_app;

-- One definition of "the records a map query covers" (ADR-032). SECURITY INVOKER and inlinable:
-- row-level security applies, and every map statement (summary, points, hexagons, beats) reads
-- the same rows, so counts, legend and status bar cannot disagree (HM-1). The p_scope_* arguments
-- are the territory of the permission in use (ADR-028); RLS is the second, independent filter.
CREATE FUNCTION core.map_selection(
    p_region uuid, p_stations uuid[], p_beats uuid[], p_categories uuid[], p_record_types text[],
    p_statuses text[], p_from timestamptz, p_to timestamptz, p_basis text, p_min_confidence numeric,
    p_scope_global boolean, p_scope_regions uuid[], p_scope_stations uuid[])
RETURNS TABLE (id uuid, region_id uuid, station_id uuid, beat_id uuid, analytical_category_id uuid,
               is_sensitive boolean, record_type text, status_code text, quality text,
               geometry geometry, x double precision, y double precision, crs integer,
               is_residential boolean, precision_level text)
LANGUAGE sql STABLE AS $$
    SELECT i.id, i.region_id, i.station_id, i.beat_id, i.analytical_category_id,
           i.is_sensitive_category, i.record_type, i.status_code, i.record_quality_status,
           l.geometry, l.analysis_x, l.analysis_y, l.analysis_crs,
           coalesce(l.is_residential, false), l.precision_level
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
$$;
GRANT EXECUTE ON FUNCTION core.map_selection(uuid, uuid[], uuid[], uuid[], text[], text[], timestamptz,
    timestamptz, text, numeric, boolean, uuid[], uuid[]) TO crimemap_app;

-- Global case-data grants (e.g. SENIOR_LEADERSHIP / DEMO_USER aggregate maps) carry no region ids, so
-- row-level security needs an explicit flag. UnitOfWork sets app.scope_global only when the caller
-- holds a GLOBAL grant of a case-data permission; the API still applies the per-permission scope and
-- aggregate-only rules (closes audit L3 for incidents).
DROP POLICY incident_app_scope ON core.incident_record;
CREATE POLICY incident_app_scope ON core.incident_record FOR SELECT TO crimemap_app
USING (
    access_classification <> 'HIGHLY_RESTRICTED'
    AND ((SELECT core.scope_global())
         OR region_id = ANY (CAST((SELECT core.scope_ids('app.region_ids')) AS uuid[]))
         OR station_id = ANY (CAST((SELECT core.scope_ids('app.station_ids')) AS uuid[])))
);

-- 2. Operational records (never an FIR) --------------------------------------------------------
CREATE TABLE core.operational_record (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    record_kind               text NOT NULL CHECK (record_kind IN (
                                  'ANALYST_NOTE', 'LOCATION_CORRECTION_REQUEST', 'APPROVED_EVENT_MARKER',
                                  'OPERATIONAL_OBSERVATION', 'ACTION_COMPLETION', 'CIVIC_CONTEXT_REFERENCE')),
    title                     text NOT NULL CHECK (length(title) BETWEEN 3 AND 200),
    occurred_at               timestamptz NOT NULL,
    geometry                  geometry(Point, 4326),
    locality                  text CHECK (locality IS NULL OR length(locality) <= 200),
    precision_level           text NOT NULL CHECK (precision_level IN ('EXACT','APPROXIMATE','GRID','UNKNOWN')),
    confidence                numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reason                    text NOT NULL CHECK (length(reason) BETWEEN 3 AND 2000),
    evidence_reference        text CHECK (evidence_reference IS NULL OR length(evidence_reference) <= 500),
    visibility_classification text NOT NULL CHECK (visibility_classification IN ('INTERNAL','RESTRICTED')),
    subject_incident_id       uuid REFERENCES core.incident_record (id),
    review_status             text NOT NULL CHECK (review_status IN ('NOT_REQUIRED','PENDING','APPROVED','REJECTED')),
    review_note               text CHECK (review_note IS NULL OR length(review_note) <= 2000),
    reviewed_by               uuid REFERENCES core.app_user (id),
    reviewed_at               timestamptz,
    region_id                 uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id                uuid REFERENCES core.organizational_unit (id),
    beat_id                   uuid REFERENCES core.organizational_unit (id),
    created_by                uuid NOT NULL REFERENCES core.app_user (id),
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    is_demo                   boolean NOT NULL DEFAULT true,
    CHECK (geometry IS NOT NULL OR locality IS NOT NULL),
    CHECK (reviewed_by IS NULL OR reviewed_by <> created_by),
    CHECK (record_kind <> 'LOCATION_CORRECTION_REQUEST' OR (subject_incident_id IS NOT NULL AND geometry IS NOT NULL))
);
CREATE INDEX operational_record_scope_idx ON core.operational_record (region_id, review_status, created_at DESC);
CREATE INDEX operational_record_geom_idx ON core.operational_record USING gist (geometry);

-- Content is fixed once submitted; only the review fields change (and only once, from PENDING).
CREATE FUNCTION core.reject_operational_record_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operational records cannot be deleted';
    END IF;
    IF OLD.review_status <> 'PENDING' THEN
        RAISE EXCEPTION 'operational record % has already been decided', OLD.id USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER operational_record_immutable BEFORE UPDATE OR DELETE ON core.operational_record
FOR EACH ROW EXECUTE FUNCTION core.reject_operational_record_change();

ALTER TABLE core.operational_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.operational_record FORCE ROW LEVEL SECURITY;
CREATE POLICY operational_record_app_scope ON core.operational_record FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY operational_record_owner ON core.operational_record TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (record_kind, title, occurred_at, geometry, locality, precision_level, confidence,
                      reason, evidence_reference, visibility_classification, subject_incident_id,
                      review_status, region_id, station_id, beat_id, created_by, is_demo)
      ON core.operational_record TO crimemap_app;
GRANT UPDATE (review_status, review_note, reviewed_by, reviewed_at, updated_at)
      ON core.operational_record TO crimemap_app;

-- 3. Analyst notes (append-only) -----------------------------------------------------------------
CREATE TABLE core.analyst_note (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_type       text NOT NULL CHECK (subject_type IN ('INCIDENT','CELL','PATTERN_REVIEW','ACTION','DQ_ISSUE')),
    subject_id         uuid,
    subject_key        text CHECK (subject_key IS NULL OR length(subject_key) <= 100),
    note               text NOT NULL CHECK (length(note) BETWEEN 1 AND 4000),
    visibility_level   text NOT NULL CHECK (visibility_level IN ('INTERNAL','RESTRICTED')),
    region_id          uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id         uuid REFERENCES core.organizational_unit (id),
    created_by         uuid NOT NULL REFERENCES core.app_user (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (subject_id IS NOT NULL OR subject_key IS NOT NULL)
);
CREATE INDEX analyst_note_subject_idx ON core.analyst_note (subject_type, subject_id, created_at);
ALTER TABLE core.analyst_note ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.analyst_note FORCE ROW LEVEL SECURITY;
CREATE POLICY analyst_note_app_scope ON core.analyst_note FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY analyst_note_owner ON core.analyst_note TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (subject_type, subject_id, subject_key, note, visibility_level, region_id,
                      station_id, created_by) ON core.analyst_note TO crimemap_app;

-- 4. User-flagged data-quality issues --------------------------------------------------------------
GRANT INSERT (incident_id, subject_type, subject_id, issue_type, severity, detected_by, rule_code,
              detail, region_id, station_id, due_at) ON core.data_quality_issue TO crimemap_app;
"""
    )


def downgrade() -> None:
    run_sql(
        """
DROP POLICY incident_location_app_scope ON core.incident_location;
CREATE POLICY incident_location_app_scope ON core.incident_location FOR SELECT TO crimemap_app
USING (EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_id));
REVOKE INSERT (incident_id, subject_type, subject_id, issue_type, severity, detected_by, rule_code,
               detail, region_id, station_id, due_at) ON core.data_quality_issue FROM crimemap_app;
DROP POLICY incident_app_scope ON core.incident_record;
CREATE POLICY incident_app_scope ON core.incident_record FOR SELECT TO crimemap_app
USING (
    access_classification <> 'HIGHLY_RESTRICTED'
    AND (region_id = ANY (core.scope_ids('app.region_ids'))
         OR station_id = ANY (core.scope_ids('app.station_ids')))
);
DROP FUNCTION core.scope_global();
DROP TABLE core.analyst_note;
DROP TABLE core.operational_record;
DROP FUNCTION core.reject_operational_record_change();
DROP FUNCTION core.map_selection(uuid, uuid[], uuid[], uuid[], text[], text[], timestamptz,
    timestamptz, text, numeric, boolean, uuid[], uuid[]);
DROP FUNCTION core.hex_polygon(integer, integer, double precision, integer);
DROP FUNCTION core.hex_cell(double precision, double precision, double precision);
DROP INDEX core.incident_location_locality_trgm_idx;
DROP INDEX core.incident_reference_prefix_idx;
DROP INDEX core.incident_station_occurrence_idx;
DROP INDEX core.incident_region_occurrence_idx;
DROP TRIGGER incident_record_scope_propagation ON core.incident_record;
DROP FUNCTION core.propagate_incident_scope();
DROP TRIGGER incident_location_sync ON core.incident_location;
DROP FUNCTION core.sync_incident_location();
ALTER TABLE core.incident_location DROP COLUMN access_classification, DROP COLUMN station_id,
    DROP COLUMN region_id, DROP COLUMN analysis_y, DROP COLUMN analysis_x, DROP COLUMN analysis_crs;
"""
    )
