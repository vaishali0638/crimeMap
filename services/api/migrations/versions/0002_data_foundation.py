"""Phase 2 data foundation: boundaries, reference data, ingestion staging, incidents, lineage,
data-quality issues, restricted details, and row-level security.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12

Writers: the worker role (crimemap_worker) loads and enriches data. The API role (crimemap_app)
reads case tables only through row-level security scoped by the per-transaction settings
app.region_ids / app.station_ids set by UnitOfWork, and never sees HIGHLY_RESTRICTED rows or the
staging/restricted schemas.
"""

from crimemap_api.migration_sql import run_sql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMAS = """
CREATE SCHEMA staging;
CREATE SCHEMA restricted;
REVOKE ALL ON SCHEMA staging, restricted FROM PUBLIC;
GRANT USAGE ON SCHEMA staging, restricted TO crimemap_worker;
"""

REFERENCE = """
-- Versioned boundaries. Demo boundaries are SYNTHETIC/DEMO_ONLY (ADR-013).
CREATE TABLE core.geographic_boundary (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_unit_id         uuid NOT NULL REFERENCES core.organizational_unit (id),
    boundary_type       text NOT NULL CHECK (boundary_type IN ('REGION','STATION','BEAT')),
    version             integer NOT NULL CHECK (version > 0),
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_to            timestamptz,
    geometry            geometry(MultiPolygon, 4326) NOT NULL
                        CHECK (ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)),
    source              text NOT NULL,
    approval_status     text NOT NULL CHECK (approval_status IN ('DEMO_ONLY','PROPOSED','APPROVED','RETIRED')),
    approved_admin_by   uuid REFERENCES core.app_user (id),
    approved_steward_by uuid REFERENCES core.app_user (id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_unit_id, version),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (approved_admin_by IS NULL OR approved_steward_by IS NULL
           OR approved_admin_by <> approved_steward_by)
);
CREATE INDEX geographic_boundary_geom_idx ON core.geographic_boundary USING gist (geometry);
-- At most one boundary in force per unit.
CREATE UNIQUE INDEX geographic_boundary_current_idx ON core.geographic_boundary (org_unit_id)
    WHERE valid_to IS NULL AND approval_status IN ('DEMO_ONLY', 'APPROVED');

CREATE VIEW core.current_boundary AS
SELECT b.id AS boundary_id, b.org_unit_id, o.unit_type, o.code, o.name, o.parent_id, b.version,
       b.geometry, b.source, b.approval_status, o.is_demo
FROM core.geographic_boundary b
JOIN core.organizational_unit o ON o.id = b.org_unit_id
WHERE b.valid_to IS NULL AND b.approval_status IN ('DEMO_ONLY', 'APPROVED')
  AND (o.active_to IS NULL OR o.active_to > current_date);

CREATE TABLE core.analytical_category (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                      text NOT NULL UNIQUE,
    name                      text NOT NULL,
    parent_id                 uuid REFERENCES core.analytical_category (id),
    is_sensitive              boolean NOT NULL DEFAULT false,
    official_mapping_version  text NOT NULL,
    active_from               date NOT NULL DEFAULT DATE '2020-01-01',
    active_to                 date
);
INSERT INTO core.analytical_category (code, name, is_sensitive, official_mapping_version) VALUES
  ('VIOLENCE_AGAINST_PERSON', 'Violence against person', false, 'DEMO-2026.1'),
  ('PROPERTY_THEFT',          'Property / theft',         false, 'DEMO-2026.1'),
  ('VEHICLE_THEFT',           'Vehicle theft',            false, 'DEMO-2026.1'),
  ('CYBER_FRAUD',             'Cyber-enabled fraud',      false, 'DEMO-2026.1'),
  ('CRIMES_AGAINST_WOMEN',    'Crimes against women',     true,  'DEMO-2026.1'),
  ('CRIMES_AGAINST_CHILDREN', 'Crimes against children',  true,  'DEMO-2026.1'),
  ('NARCOTICS',               'Narcotics',                false, 'DEMO-2026.1'),
  ('PUBLIC_ORDER',            'Public order',             false, 'DEMO-2026.1'),
  ('TRAFFIC_ROAD_SAFETY',     'Traffic / road safety',    false, 'DEMO-2026.1'),
  ('SPECIAL_LOCAL_LAWS',      'Special / local laws',     false, 'DEMO-2026.1'),
  ('OTHER',                   'Other',                    false, 'DEMO-2026.1');

-- Official offence code -> analytical category, versioned. Demo codes are fictional ("D-...").
CREATE TABLE core.offence_category_map (
    mapping_version         text NOT NULL,
    offence_code            text NOT NULL,
    analytical_category_id  uuid NOT NULL REFERENCES core.analytical_category (id),
    PRIMARY KEY (mapping_version, offence_code)
);

CREATE TABLE core.status_definition (
    record_type  text NOT NULL,
    code         text NOT NULL,
    label        text NOT NULL,
    is_open      boolean NOT NULL,
    PRIMARY KEY (record_type, code)
);
INSERT INTO core.status_definition VALUES
  ('CASE', 'REGISTERED', 'Registered', true),
  ('CASE', 'UNDER_INVESTIGATION', 'Under investigation', true),
  ('CASE', 'DETECTED', 'Detected', true),
  ('CASE', 'CLOSED', 'Closed', false),
  ('ERSS_CALL', 'RECEIVED', 'Received', true),
  ('ERSS_CALL', 'ATTENDED', 'Attended', false),
  ('ACCIDENT', 'REPORTED', 'Reported', true),
  ('ACCIDENT', 'CLOSED', 'Closed', false),
  ('TRAFFIC_EVENT', 'REPORTED', 'Reported', true),
  ('TRAFFIC_EVENT', 'CLEARED', 'Cleared', false),
  ('OPERATIONAL_OBSERVATION', 'RECORDED', 'Recorded', false),
  ('CIVIC_CONTEXT', 'VERIFIED', 'Verified', true);

CREATE TABLE core.source_system (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                   text NOT NULL UNIQUE,
    name                   text NOT NULL,
    classification         text NOT NULL,
    approved_status        text NOT NULL CHECK (approved_status IN ('DEMO','NOT_APPROVED','APPROVED','SUSPENDED')),
    adapter_type           text NOT NULL,
    owner                  text NOT NULL,
    sync_policy            jsonb NOT NULL DEFAULT '{}',
    last_batch_at          timestamptz,
    last_health_check_at   timestamptz,
    last_health_status     text CHECK (last_health_status IN ('OK','STALE','FAILING','NEVER_RUN','DISABLED'))
);
INSERT INTO core.source_system (code, name, classification, approved_status, adapter_type, owner, sync_policy, last_health_status) VALUES
  ('DEMO_SYNTHETIC', 'Synthetic demo incident feed', 'RESTRICTED', 'DEMO', 'signed-json-batch', 'CRIMEMAP project (demo)', '{"stale_after_hours": 48, "mapping_version": "DEMO-2026.1"}', 'NEVER_RUN'),
  ('DEMO_SYNTHETIC_LEGACY', 'Synthetic legacy feed (deliberately stale)', 'RESTRICTED', 'DEMO', 'signed-json-batch', 'CRIMEMAP project (demo)', '{"stale_after_hours": 48, "mapping_version": "DEMO-2026.1"}', 'NEVER_RUN'),
  ('CCTNS', 'CCTNS (not connected)', 'HIGHLY_RESTRICTED', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED'),
  ('ERSS_112', 'ERSS-112 (not connected)', 'RESTRICTED', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED'),
  ('ICJS', 'ICJS (not connected)', 'RESTRICTED', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED'),
  ('EDAR', 'eDAR (not connected)', 'RESTRICTED', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED'),
  ('ECHALLAN', 'e-challan (not connected)', 'INTERNAL', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED'),
  ('CCTV_ICCC', 'CCTV / ICCC metadata (not connected)', 'RESTRICTED', 'NOT_APPROVED', 'interface-stub', 'Not assigned', '{}', 'DISABLED');

CREATE TABLE core.feature_flag (
    key         text PRIMARY KEY,
    enabled     boolean NOT NULL,
    changed_by  uuid REFERENCES core.app_user (id),
    changed_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO core.feature_flag (key, enabled) VALUES ('civic_context', false);

-- Approved denominators for rates (ADR-014). Empty in the demo: rates show "rate unavailable".
CREATE TABLE core.denominator (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_unit_id      uuid NOT NULL REFERENCES core.organizational_unit (id),
    measure          text NOT NULL,
    value            numeric NOT NULL CHECK (value > 0),
    period_start     date NOT NULL,
    period_end       date NOT NULL,
    source           text NOT NULL,
    approval_status  text NOT NULL CHECK (approval_status IN ('PROPOSED','APPROVED','RETIRED'))
);
"""

STAGING = r"""
CREATE TABLE staging.ingest_batch (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id  uuid NOT NULL REFERENCES core.source_system (id),
    batch_ref         text NOT NULL,
    received_at       timestamptz NOT NULL DEFAULT now(),
    source_extracted_at timestamptz,  -- when the source says it extracted the data (freshness)
    signature_valid   boolean NOT NULL,
    record_count      integer NOT NULL CHECK (record_count >= 0),
    accepted          integer NOT NULL DEFAULT 0,
    duplicates        integer NOT NULL DEFAULT 0,
    rejected          integer NOT NULL DEFAULT 0,
    status            text NOT NULL DEFAULT 'RECEIVED'
                      CHECK (status IN ('RECEIVED','PROCESSED','REJECTED_SIGNATURE','FAILED')),
    completed_at      timestamptz,
    UNIQUE (source_system_id, batch_ref)
);

-- Raw source records, exactly as received. Only processing-state columns may change.
CREATE TABLE staging.raw_ingest (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id          uuid NOT NULL REFERENCES staging.ingest_batch (id),
    source_system_id  uuid NOT NULL REFERENCES core.source_system (id),
    received_at       timestamptz NOT NULL DEFAULT now(),
    payload           jsonb NOT NULL,
    content_hash      text NOT NULL,
    idempotency_key   text NOT NULL UNIQUE,
    status            text NOT NULL DEFAULT 'RECEIVED'
                      CHECK (status IN ('RECEIVED','VALID','INVALID','PROCESSED','SUPERSEDED')),
    error             text,
    processed_at      timestamptz
);
CREATE INDEX raw_ingest_batch_status_idx ON staging.raw_ingest (batch_id, status);

CREATE TABLE staging.dead_letter (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_ingest_id   uuid NOT NULL UNIQUE REFERENCES staging.raw_ingest (id),
    error_code      text NOT NULL,
    error_detail    text NOT NULL,
    attempts        integer NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz
);
"""

CASES = """
CREATE TABLE core.incident_record (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id            uuid NOT NULL REFERENCES core.source_system (id),
    source_record_reference     text NOT NULL,
    source_version              integer NOT NULL CHECK (source_version > 0),
    record_type                 text NOT NULL CHECK (record_type IN ('CASE','ERSS_CALL','ACCIDENT','TRAFFIC_EVENT','CIVIC_CONTEXT','OPERATIONAL_OBSERVATION')),
    official_offence_act        text,
    official_offence_section    text,
    official_offence_name       text,
    analytical_category_id      uuid REFERENCES core.analytical_category (id),
    occurrence_start_at         timestamptz,
    occurrence_end_at           timestamptz,
    reported_at                 timestamptz NOT NULL,
    status_code                 text,
    reporting_station_id        uuid REFERENCES core.organizational_unit (id),
    investigating_unit_id       uuid REFERENCES core.organizational_unit (id),
    occurrence_jurisdiction_id  uuid REFERENCES core.organizational_unit (id),
    region_id                   uuid REFERENCES core.organizational_unit (id),
    station_id                  uuid REFERENCES core.organizational_unit (id),
    beat_id                     uuid REFERENCES core.organizational_unit (id),
    sensitivity_classification  text NOT NULL CHECK (sensitivity_classification IN ('INTERNAL','RESTRICTED','HIGHLY_RESTRICTED')),
    access_classification       text NOT NULL CHECK (access_classification IN ('INTERNAL','RESTRICTED','HIGHLY_RESTRICTED')),
    is_sensitive_category       boolean NOT NULL DEFAULT false,
    record_quality_status       text NOT NULL DEFAULT 'OK' CHECK (record_quality_status IN ('OK','WARNING','FAILED')),
    is_demo                     boolean NOT NULL,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, source_record_reference),
    CHECK (occurrence_end_at IS NULL OR occurrence_start_at IS NULL OR occurrence_end_at >= occurrence_start_at)
);
CREATE INDEX incident_scope_idx ON core.incident_record (station_id, analytical_category_id, occurrence_start_at);
CREATE INDEX incident_region_idx ON core.incident_record (region_id, reported_at);

-- One location per incident (1:1, keyed by incident). The specification's IncidentRecord.geometry_id
-- is this row.
CREATE TABLE core.incident_location (
    incident_id       uuid PRIMARY KEY REFERENCES core.incident_record (id) ON DELETE CASCADE,
    geometry          geometry(Point, 4326),
    location_text     text,
    locality          text,
    precision_level   text NOT NULL CHECK (precision_level IN ('EXACT','APPROXIMATE','GRID','UNKNOWN')),
    is_residential    boolean NOT NULL DEFAULT false,
    confidence_score  numeric(3,2) CHECK (confidence_score BETWEEN 0 AND 1),
    geocode_method    text NOT NULL CHECK (geocode_method IN ('SOURCE_COORDINATE','LOCALITY_CENTROID','MANUAL','SUGGESTED','NONE')),
    reviewer_status   text NOT NULL DEFAULT 'UNREVIEWED' CHECK (reviewer_status IN ('UNREVIEWED','CONFIRMED','CORRECTED')),
    verified_at       timestamptz
);
CREATE INDEX incident_location_geom_idx ON core.incident_location USING gist (geometry);

CREATE TABLE core.source_record_link (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system_id         uuid NOT NULL REFERENCES core.source_system (id),
    source_record_reference  text NOT NULL,
    source_version           integer NOT NULL,
    incident_id              uuid REFERENCES core.incident_record (id),
    raw_ingest_id            uuid NOT NULL,
    received_at              timestamptz NOT NULL,
    content_hash             text NOT NULL,
    lineage                  jsonb NOT NULL,
    UNIQUE (source_system_id, source_record_reference, source_version)
);

CREATE TABLE core.data_quality_issue (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id      uuid REFERENCES core.incident_record (id),
    subject_type     text NOT NULL CHECK (subject_type IN ('INCIDENT','SOURCE_SYSTEM','BOUNDARY')),
    subject_id       uuid NOT NULL,
    issue_type       text NOT NULL CHECK (issue_type IN ('MISSING_LOCATION','LOW_CONFIDENCE_LOCATION','MISSING_OCCURRENCE_TIME','POSSIBLE_DUPLICATE','INVALID_CATEGORY_MAPPING','STALE_SOURCE','OUTSIDE_JURISDICTION','OTHER')),
    severity         text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH')),
    detected_by      text NOT NULL CHECK (detected_by IN ('RULE','AI','USER')),
    rule_code        text NOT NULL,
    state            text NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','ASSIGNED','RESOLVED','ACCEPTED_AS_IS')),
    assigned_to      uuid REFERENCES core.app_user (id),
    due_at           timestamptz,
    resolution_note  text CHECK (resolution_note IS NULL OR length(resolution_note) <= 2000),
    detail           jsonb NOT NULL DEFAULT '{}',
    region_id        uuid REFERENCES core.organizational_unit (id),
    station_id       uuid REFERENCES core.organizational_unit (id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, rule_code)
);
CREATE INDEX dq_issue_queue_idx ON core.data_quality_issue (state, region_id, created_at);

-- Highly restricted source details: synthetic tokens only in the demo; no API access at all.
CREATE TABLE restricted.sensitive_source_detail (
    incident_id               uuid PRIMARY KEY REFERENCES core.incident_record (id) ON DELETE CASCADE,
    victim_reference_token    text,
    witness_reference_token   text,
    residential_location      geometry(Point, 4326)
);
"""

SECURITY = """
-- Raw payloads are immutable: the worker may only change processing state.
CREATE FUNCTION staging.reject_raw_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'raw ingest records cannot be deleted';
    END IF;
    IF NEW.payload IS DISTINCT FROM OLD.payload OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key THEN
        RAISE EXCEPTION 'raw ingest payloads are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER raw_ingest_immutable BEFORE UPDATE OR DELETE ON staging.raw_ingest
FOR EACH ROW EXECUTE FUNCTION staging.reject_raw_change();

-- Row-level security on case data (ADR-002). Settings are transaction-local, set by UnitOfWork.
ALTER TABLE core.incident_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.incident_record FORCE ROW LEVEL SECURITY;
ALTER TABLE core.incident_location ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.incident_location FORCE ROW LEVEL SECURITY;
ALTER TABLE core.data_quality_issue ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.data_quality_issue FORCE ROW LEVEL SECURITY;

CREATE FUNCTION core.scope_ids(setting text) RETURNS uuid[] LANGUAGE sql STABLE AS $$
    SELECT coalesce(string_to_array(nullif(current_setting(setting, true), ''), ',')::uuid[], '{}')
$$;

CREATE POLICY incident_app_scope ON core.incident_record FOR SELECT TO crimemap_app
USING (
    access_classification <> 'HIGHLY_RESTRICTED'
    AND (region_id = ANY (core.scope_ids('app.region_ids'))
         OR station_id = ANY (core.scope_ids('app.station_ids')))
);
CREATE POLICY incident_location_app_scope ON core.incident_location FOR SELECT TO crimemap_app
USING (EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_id));
-- Source-level issues (e.g. a stale feed) carry no territory and no case data; the dq.manage
-- permission check in the API still decides who may list them.
CREATE POLICY dq_app_scope ON core.data_quality_issue FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids'))
       OR station_id = ANY (core.scope_ids('app.station_ids'))
       OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids'))
            OR station_id = ANY (core.scope_ids('app.station_ids'))
            OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL));

-- Ingestion visibility for the API without granting the staging schema: views run with their
-- owner's privileges and expose counts and timestamps only, never payloads.
CREATE VIEW core.source_health AS
SELECT s.id AS source_system_id, s.code, s.name, s.approved_status, s.adapter_type,
       s.last_batch_at, s.last_health_check_at, s.last_health_status,
       (s.sync_policy->>'stale_after_hours')::int AS stale_after_hours,
       lb.batch_ref AS last_batch_ref, lb.received_at AS last_received_at,
       lb.record_count AS last_record_count, lb.accepted AS last_accepted,
       lb.duplicates AS last_duplicates, lb.rejected AS last_rejected, lb.status AS last_batch_status,
       (SELECT count(*) FROM staging.dead_letter d JOIN staging.raw_ingest r ON r.id = d.raw_ingest_id
        WHERE r.source_system_id = s.id AND d.resolved_at IS NULL) AS open_dead_letters
FROM core.source_system s
LEFT JOIN LATERAL (SELECT * FROM staging.ingest_batch b WHERE b.source_system_id = s.id
                   ORDER BY b.received_at DESC LIMIT 1) lb ON true;

CREATE VIEW core.ingest_reconciliation AS
SELECT s.code AS source_code, (b.received_at AT TIME ZONE 'UTC')::date AS received_on,
       count(*) AS batches, sum(b.record_count) AS declared, sum(b.accepted) AS accepted,
       sum(b.duplicates) AS duplicates, sum(b.rejected) AS rejected,
       sum(b.record_count) - sum(b.accepted) - sum(b.duplicates) - sum(b.rejected) AS unexplained,
       count(*) FILTER (WHERE NOT b.signature_valid) AS rejected_batches
FROM staging.ingest_batch b JOIN core.source_system s ON s.id = b.source_system_id
GROUP BY s.code, (b.received_at AT TIME ZONE 'UTC')::date;

-- The worker loads data for all territories; the owner keeps access for administration.
CREATE POLICY incident_worker ON core.incident_record TO crimemap_worker USING (true) WITH CHECK (true);
CREATE POLICY incident_location_worker ON core.incident_location TO crimemap_worker USING (true) WITH CHECK (true);
CREATE POLICY dq_worker ON core.data_quality_issue TO crimemap_worker USING (true) WITH CHECK (true);
CREATE POLICY incident_owner ON core.incident_record TO crimemap_migrator USING (true) WITH CHECK (true);
CREATE POLICY incident_location_owner ON core.incident_location TO crimemap_migrator USING (true) WITH CHECK (true);
CREATE POLICY dq_owner ON core.data_quality_issue TO crimemap_migrator USING (true) WITH CHECK (true);

-- Grants. API role: read reference data and scoped case data; change DQ issue state only.
REVOKE ALL ON ALL TABLES IN SCHEMA core, staging, restricted FROM PUBLIC;
GRANT SELECT ON core.geographic_boundary, core.current_boundary, core.analytical_category,
                core.offence_category_map, core.status_definition, core.source_system,
                core.feature_flag, core.denominator, core.incident_record, core.incident_location,
                core.source_record_link, core.data_quality_issue, core.source_health,
                core.ingest_reconciliation TO crimemap_app;
GRANT UPDATE (state, assigned_to, resolution_note, updated_at) ON core.data_quality_issue TO crimemap_app;
GRANT EXECUTE ON FUNCTION core.scope_ids(text) TO crimemap_app, crimemap_worker;

-- Worker role: ingestion and enrichment.
GRANT SELECT ON core.organizational_unit, core.geographic_boundary, core.current_boundary,
                core.analytical_category, core.offence_category_map, core.status_definition
             TO crimemap_worker;
GRANT SELECT, UPDATE (last_batch_at, last_health_check_at, last_health_status) ON core.source_system TO crimemap_worker;
GRANT SELECT, INSERT, UPDATE ON core.incident_record, core.incident_location, core.data_quality_issue TO crimemap_worker;
GRANT SELECT, INSERT ON core.source_record_link TO crimemap_worker;
GRANT SELECT, INSERT, UPDATE ON staging.ingest_batch, staging.dead_letter TO crimemap_worker;
GRANT SELECT, INSERT ON staging.raw_ingest TO crimemap_worker;
GRANT UPDATE (status, error, processed_at) ON staging.raw_ingest TO crimemap_worker;
GRANT INSERT, UPDATE ON restricted.sensitive_source_detail TO crimemap_worker;
-- ON CONFLICT needs SELECT on the arbiter column only; token values stay unreadable to the worker.
GRANT SELECT (incident_id) ON restricted.sensitive_source_detail TO crimemap_worker;

-- Boundary approval (dual control). A proposed version comes into force only after one SYSTEM_ADMIN
-- and one DATA_STEWARD, who must be different people, have both approved it. The API decides which
-- slot the caller may fill (policy engine); this function enforces the state machine atomically, so
-- the API role needs no UPDATE privilege on boundaries.
CREATE FUNCTION core.approve_boundary(p_boundary uuid, p_approver uuid, p_slot text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, core AS $$
DECLARE
    b core.geographic_boundary%ROWTYPE;
BEGIN
    IF p_slot NOT IN ('ADMIN', 'STEWARD') THEN
        RAISE EXCEPTION 'invalid approval slot' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO b FROM core.geographic_boundary WHERE id = p_boundary FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'boundary not found' USING ERRCODE = 'P0002';
    END IF;
    IF b.approval_status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'boundary is not awaiting approval' USING ERRCODE = '55000';
    END IF;
    IF (p_slot = 'ADMIN' AND b.approved_admin_by IS NOT NULL)
       OR (p_slot = 'STEWARD' AND b.approved_steward_by IS NOT NULL) THEN
        RAISE EXCEPTION 'this approval has already been given' USING ERRCODE = '55000';
    END IF;
    IF p_approver = b.approved_admin_by OR p_approver = b.approved_steward_by THEN
        RAISE EXCEPTION 'the second approval must come from a different person' USING ERRCODE = '42501';
    END IF;
    IF p_slot = 'ADMIN' THEN
        UPDATE core.geographic_boundary SET approved_admin_by = p_approver WHERE id = p_boundary;
    ELSE
        UPDATE core.geographic_boundary SET approved_steward_by = p_approver WHERE id = p_boundary;
    END IF;
    SELECT * INTO b FROM core.geographic_boundary WHERE id = p_boundary;
    IF b.approved_admin_by IS NOT NULL AND b.approved_steward_by IS NOT NULL THEN
        UPDATE core.geographic_boundary SET valid_to = now(), approval_status = 'RETIRED'
        WHERE org_unit_id = b.org_unit_id AND id <> b.id AND valid_to IS NULL
          AND approval_status IN ('DEMO_ONLY', 'APPROVED');
        UPDATE core.geographic_boundary SET approval_status = 'APPROVED', valid_from = now()
        WHERE id = p_boundary;
        RETURN 'APPROVED';
    END IF;
    RETURN 'PROPOSED';
END $$;
REVOKE ALL ON FUNCTION core.approve_boundary(uuid, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.approve_boundary(uuid, uuid, text) TO crimemap_app;
"""


def upgrade() -> None:
    for block in (SCHEMAS, REFERENCE, STAGING, CASES, SECURITY):
        run_sql(block)


def downgrade() -> None:
    run_sql(
        """
        DROP VIEW core.ingest_reconciliation, core.source_health;
        DROP SCHEMA restricted CASCADE;
        DROP SCHEMA staging CASCADE;
        DROP TABLE core.data_quality_issue, core.source_record_link, core.incident_location,
                   core.incident_record, core.denominator, core.feature_flag, core.source_system,
                   core.status_definition, core.offence_category_map, core.analytical_category
                   CASCADE;
        DROP FUNCTION IF EXISTS core.approve_boundary(uuid, uuid, text);
        DROP VIEW core.current_boundary;
        DROP TABLE core.geographic_boundary;
        DROP FUNCTION core.scope_ids(text);
        """
    )
