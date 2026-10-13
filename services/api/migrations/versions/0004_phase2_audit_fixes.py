"""Phase 2 audit fixes (docs/phase-2-audit.md).

- Worker may read the reconciliation view (daily reconciliation report).
- Raw records keep the payload exactly as received; a separate ``canonical`` column holds the
  validated, normalised record that the pipeline reads (a lax-parsed value can no longer abort a
  whole batch in SQL).
- Batches record a content hash, so a re-used batch reference with different content is detected.
- Lookup indexes for version-conflict detection and source health.
- Row-level security on lineage links (previously readable in full by the API role).
- Data-quality issues on records the caller cannot see are invisible and unchangeable.
- Boundary approval takes the approver from the session (app.user_id) and checks the approver's
  status and role grant itself; custom SQLSTATEs for its refusals.
- Incident quality status is recomputed whenever an issue changes state (incl. manual review).
- Exact residential locations move to the restricted schema; the analytical store keeps a
  generalised point (about 250 m grid, kept inside the assigned beat). Enforced by a CHECK.
- Possible-duplicate details store the partner's id, not its case reference.
- The PRODUCTION guard also detects DEMO_ONLY boundaries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
GRANT SELECT ON core.ingest_reconciliation TO crimemap_worker;

ALTER TABLE staging.raw_ingest ADD COLUMN canonical jsonb;
GRANT UPDATE (canonical) ON staging.raw_ingest TO crimemap_worker;
ALTER TABLE staging.ingest_batch ADD COLUMN content_sha256 text;

CREATE INDEX raw_ingest_ref_version_idx ON staging.raw_ingest
    (source_system_id, (payload->>'source_record_reference'), (payload->>'source_version'));
CREATE INDEX ingest_batch_source_received_idx ON staging.ingest_batch (source_system_id, received_at DESC);

-- Source health describes the last *verified* batch; signature rejections are counted in
-- reconciliation (they are attributed to the source the unverified file claims).
CREATE OR REPLACE VIEW core.source_health AS
SELECT s.id AS source_system_id, s.code, s.name, s.approved_status, s.adapter_type,
       s.last_batch_at, s.last_health_check_at, s.last_health_status,
       (s.sync_policy->>'stale_after_hours')::int AS stale_after_hours,
       lb.batch_ref AS last_batch_ref, lb.received_at AS last_received_at,
       lb.record_count AS last_record_count, lb.accepted AS last_accepted,
       lb.duplicates AS last_duplicates, lb.rejected AS last_rejected, lb.status AS last_batch_status,
       (SELECT count(*) FROM staging.dead_letter d JOIN staging.raw_ingest r ON r.id = d.raw_ingest_id
        WHERE r.source_system_id = s.id AND d.resolved_at IS NULL) AS open_dead_letters
FROM core.source_system s
LEFT JOIN LATERAL (SELECT b.batch_ref, b.received_at, b.record_count, b.accepted, b.duplicates,
                          b.rejected, b.status FROM staging.ingest_batch b WHERE b.source_system_id = s.id AND b.signature_valid
                   ORDER BY b.received_at DESC LIMIT 1) lb ON true;

-- Lineage links reveal which records exist: same visibility as the incident they belong to.
ALTER TABLE core.source_record_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.source_record_link FORCE ROW LEVEL SECURITY;
CREATE POLICY link_app_scope ON core.source_record_link FOR SELECT TO crimemap_app
USING (incident_id IS NOT NULL AND EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_id));
CREATE POLICY link_worker ON core.source_record_link TO crimemap_worker USING (true) WITH CHECK (true);
CREATE POLICY link_owner ON core.source_record_link TO crimemap_migrator USING (true) WITH CHECK (true);

-- An issue about a record the caller cannot see (e.g. HIGHLY_RESTRICTED) is not listed or changeable.
DROP POLICY dq_app_scope ON core.data_quality_issue;
CREATE POLICY dq_app_scope ON core.data_quality_issue FOR ALL TO crimemap_app
USING ((region_id = ANY (core.scope_ids('app.region_ids'))
        OR station_id = ANY (core.scope_ids('app.station_ids'))
        OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL))
       AND (incident_id IS NULL OR EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_id)))
WITH CHECK ((region_id = ANY (core.scope_ids('app.region_ids'))
             OR station_id = ANY (core.scope_ids('app.station_ids'))
             OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL))
            AND (incident_id IS NULL OR EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_id)));

-- Boundary approval: the approver is the session user, never a parameter.
DROP FUNCTION core.approve_boundary(uuid, uuid, text);
CREATE FUNCTION core.approve_boundary(p_boundary uuid, p_slot text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, core AS $$
DECLARE
    b core.geographic_boundary%ROWTYPE;
    v_user uuid := nullif(current_setting('app.user_id', true), '')::uuid;
    v_region uuid;
    v_role text;
BEGIN
    IF p_slot NOT IN ('ADMIN', 'STEWARD') THEN
        RAISE EXCEPTION 'invalid approval slot' USING ERRCODE = '22023';
    END IF;
    IF v_user IS NULL OR NOT EXISTS (
        SELECT 1 FROM core.app_user WHERE id = v_user AND status = 'ACTIVE') THEN
        RAISE EXCEPTION 'no active approver in this session' USING ERRCODE = 'CM002';
    END IF;
    SELECT * INTO b FROM core.geographic_boundary WHERE id = p_boundary FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'boundary not found' USING ERRCODE = 'P0002';
    END IF;
    SELECT CASE o.unit_type WHEN 'REGION' THEN o.id WHEN 'STATION' THEN p1.id ELSE p2.id END
      INTO v_region
      FROM core.organizational_unit o
      LEFT JOIN core.organizational_unit p1 ON p1.id = o.parent_id
      LEFT JOIN core.organizational_unit p2 ON p2.id = p1.parent_id
     WHERE o.id = b.org_unit_id;
    v_role := CASE p_slot WHEN 'ADMIN' THEN 'SYSTEM_ADMIN' ELSE 'DATA_STEWARD' END;
    IF NOT EXISTS (
        SELECT 1 FROM core.user_role_grant g
        WHERE g.user_id = v_user AND g.role_code = v_role AND g.revoked_at IS NULL
          AND g.valid_from <= now() AND (g.valid_to IS NULL OR g.valid_to > now())
          AND (p_slot = 'ADMIN' OR v_region = ANY (g.scope_region_ids))) THEN
        RAISE EXCEPTION 'approver does not hold the role for this slot' USING ERRCODE = 'CM002';
    END IF;
    IF b.approval_status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'boundary is not awaiting approval' USING ERRCODE = '55000';
    END IF;
    IF (p_slot = 'ADMIN' AND b.approved_admin_by IS NOT NULL)
       OR (p_slot = 'STEWARD' AND b.approved_steward_by IS NOT NULL) THEN
        RAISE EXCEPTION 'this approval has already been given' USING ERRCODE = '55000';
    END IF;
    IF v_user = b.approved_admin_by OR v_user = b.approved_steward_by THEN
        RAISE EXCEPTION 'the second approval must come from a different person' USING ERRCODE = 'CM001';
    END IF;
    IF p_slot = 'ADMIN' THEN
        UPDATE core.geographic_boundary SET approved_admin_by = v_user WHERE id = p_boundary;
    ELSE
        UPDATE core.geographic_boundary SET approved_steward_by = v_user WHERE id = p_boundary;
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
REVOKE ALL ON FUNCTION core.approve_boundary(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION core.approve_boundary(uuid, text) TO crimemap_app;

-- Quality status follows every issue state change, including manual review in the API.
CREATE FUNCTION core.refresh_quality_status() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, core AS $$
BEGIN
    UPDATE core.incident_record i SET record_quality_status = CASE
        WHEN EXISTS (SELECT 1 FROM core.data_quality_issue q WHERE q.incident_id = i.id
                     AND q.state IN ('OPEN', 'ASSIGNED') AND q.severity = 'HIGH') THEN 'FAILED'
        WHEN EXISTS (SELECT 1 FROM core.data_quality_issue q WHERE q.incident_id = i.id
                     AND q.state IN ('OPEN', 'ASSIGNED')) THEN 'WARNING'
        ELSE 'OK' END
    WHERE i.id = NEW.incident_id;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION core.refresh_quality_status() FROM PUBLIC;
CREATE TRIGGER dq_issue_refresh_quality AFTER UPDATE OF state ON core.data_quality_issue
FOR EACH ROW WHEN (NEW.incident_id IS NOT NULL AND OLD.state IS DISTINCT FROM NEW.state)
EXECUTE FUNCTION core.refresh_quality_status();

-- Residential locations: generalise to a ~250 m grid point, kept inside the assigned beat.
CREATE FUNCTION core.generalise_point(p geometry, p_beat uuid) RETURNS geometry
LANGUAGE sql STABLE SET search_path = pg_catalog, public, core AS $$
    -- A grid point outside the beat is replaced by the nearest point of a slightly inset beat
    -- (a point exactly on the edge may not count as covered); if the inset is empty, any interior
    -- point of the beat.
    SELECT CASE WHEN p IS NULL THEN NULL
                WHEN b.geometry IS NULL OR ST_Covers(b.geometry, s.g) THEN s.g
                WHEN ST_Covers(b.geometry, c.cp) THEN c.cp
                ELSE ST_PointOnSurface(b.geometry) END
    FROM (SELECT ST_SnapToGrid(p, 0.0025) AS g) s
    LEFT JOIN core.current_boundary b ON b.org_unit_id = p_beat
    LEFT JOIN LATERAL (SELECT ST_ClosestPoint(ST_Buffer(b.geometry, -0.00001), s.g) AS cp) c ON true
$$;
GRANT EXECUTE ON FUNCTION core.generalise_point(geometry, uuid) TO crimemap_worker;

INSERT INTO restricted.sensitive_source_detail (incident_id, residential_location)
SELECT l.incident_id, l.geometry FROM core.incident_location l
WHERE l.is_residential AND l.geometry IS NOT NULL
ON CONFLICT (incident_id) DO UPDATE SET residential_location = EXCLUDED.residential_location;
UPDATE core.incident_location l
SET geometry = core.generalise_point(l.geometry, i.beat_id), precision_level = 'GRID'
FROM core.incident_record i
WHERE i.id = l.incident_id AND l.is_residential AND l.geometry IS NOT NULL;
UPDATE core.incident_location SET precision_level = 'UNKNOWN'
WHERE is_residential AND geometry IS NULL AND precision_level NOT IN ('GRID', 'UNKNOWN');
ALTER TABLE core.incident_location ADD CONSTRAINT residential_is_generalised
    CHECK (NOT is_residential OR precision_level IN ('GRID', 'UNKNOWN'));

-- Duplicate details held the partner's case reference; keep only its internal id (the API shows
-- the reference only to callers who may see that record).
UPDATE core.data_quality_issue q
SET detail = jsonb_build_object('duplicate_of_id', o.id, 'distance_m', q.detail->'distance_m')
FROM core.incident_record t, core.incident_record o
WHERE q.rule_code = 'DQ-DUP-001' AND q.detail ? 'duplicate_of' AND t.id = q.incident_id
  AND o.source_system_id = t.source_system_id AND o.source_record_reference = q.detail->>'duplicate_of';

CREATE OR REPLACE FUNCTION core.demo_data_present() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, core AS $$
    SELECT EXISTS (SELECT 1 FROM core.incident_record WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.organizational_unit WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.app_user WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.geographic_boundary WHERE approval_status = 'DEMO_ONLY')
        OR EXISTS (SELECT 1 FROM core.source_system WHERE approved_status = 'DEMO' AND last_batch_at IS NOT NULL)
$$;
"""
    )


def downgrade() -> None:
    run_sql(
        """
CREATE OR REPLACE FUNCTION core.demo_data_present() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, core AS $$
    SELECT EXISTS (SELECT 1 FROM core.incident_record WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.organizational_unit WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.app_user WHERE is_demo)
        OR EXISTS (SELECT 1 FROM core.source_system WHERE approved_status = 'DEMO' AND last_batch_at IS NOT NULL)
$$;
ALTER TABLE core.incident_location DROP CONSTRAINT residential_is_generalised;
UPDATE core.incident_location l SET geometry = d.residential_location, precision_level = 'EXACT'
FROM restricted.sensitive_source_detail d
WHERE d.incident_id = l.incident_id AND l.is_residential AND d.residential_location IS NOT NULL;
DROP FUNCTION core.generalise_point(geometry, uuid);
DROP TRIGGER dq_issue_refresh_quality ON core.data_quality_issue;
DROP FUNCTION core.refresh_quality_status();
DROP FUNCTION core.approve_boundary(uuid, text);
CREATE FUNCTION core.approve_boundary(p_boundary uuid, p_approver uuid, p_slot text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, core AS $$
BEGIN
    RAISE EXCEPTION 'boundary approval is unavailable after downgrade; re-apply migration 0004'
        USING ERRCODE = '55000';
END $$;
REVOKE ALL ON FUNCTION core.approve_boundary(uuid, uuid, text) FROM PUBLIC;
DROP POLICY dq_app_scope ON core.data_quality_issue;
CREATE POLICY dq_app_scope ON core.data_quality_issue FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids'))
       OR station_id = ANY (core.scope_ids('app.station_ids'))
       OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids'))
            OR station_id = ANY (core.scope_ids('app.station_ids'))
            OR (subject_type = 'SOURCE_SYSTEM' AND region_id IS NULL AND station_id IS NULL));
DROP POLICY link_owner ON core.source_record_link;
DROP POLICY link_worker ON core.source_record_link;
DROP POLICY link_app_scope ON core.source_record_link;
ALTER TABLE core.source_record_link NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core.source_record_link DISABLE ROW LEVEL SECURITY;
DROP INDEX staging.ingest_batch_source_received_idx;
DROP INDEX staging.raw_ingest_ref_version_idx;
ALTER TABLE staging.ingest_batch DROP COLUMN content_sha256;
REVOKE UPDATE (canonical) ON staging.raw_ingest FROM crimemap_worker;
ALTER TABLE staging.raw_ingest DROP COLUMN canonical;
REVOKE SELECT ON core.ingest_reconciliation FROM crimemap_worker;
CREATE OR REPLACE VIEW core.source_health AS
SELECT s.id AS source_system_id, s.code, s.name, s.approved_status, s.adapter_type,
       s.last_batch_at, s.last_health_check_at, s.last_health_status,
       (s.sync_policy->>'stale_after_hours')::int AS stale_after_hours,
       lb.batch_ref AS last_batch_ref, lb.received_at AS last_received_at,
       lb.record_count AS last_record_count, lb.accepted AS last_accepted,
       lb.duplicates AS last_duplicates, lb.rejected AS last_rejected, lb.status AS last_batch_status,
       (SELECT count(*) FROM staging.dead_letter d JOIN staging.raw_ingest r ON r.id = d.raw_ingest_id
        WHERE r.source_system_id = s.id AND d.resolved_at IS NULL) AS open_dead_letters
FROM core.source_system s
LEFT JOIN LATERAL (SELECT b.batch_ref, b.received_at, b.record_count, b.accepted, b.duplicates,
                          b.rejected, b.status FROM staging.ingest_batch b WHERE b.source_system_id = s.id
                   ORDER BY b.received_at DESC LIMIT 1) lb ON true;
"""
    )
