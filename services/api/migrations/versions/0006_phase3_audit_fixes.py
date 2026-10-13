"""Phase 3 audit fixes: database-level enforcement for the Home Map write paths.

- Location scope copies: the sync trigger locks the incident row (FOR SHARE) while it copies the
  scope, so a concurrent move/reclassification of the incident waits and then propagates; before,
  two concurrent transactions could leave a HIGHLY_RESTRICTED incident's location readable under
  its old region (found by the Phase 3 audit).
- Operational records: the database, not only the API, enforces that a new record starts
  PENDING or NOT_REQUIRED according to its kind, is authored by the session user, refers only to
  an incident that user can see in the same region, and is decided once, to APPROVED or
  REJECTED, by a different session user, with the reviewer and time set.
- Analyst notes: authored by the session user.
- User-flagged data-quality issues: the API role may insert only USER issues about an incident,
  in that incident's region, with a USER- rule code; it can no longer pose as a rule or the AI.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
-- 1. Scope copies under concurrency ------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.sync_incident_location() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, core AS $$
DECLARE
    v_crs integer;
    v_p geometry;
BEGIN
    -- FOR SHARE conflicts with the row lock an UPDATE of the incident takes, so this waits for a
    -- concurrent move/reclassification to commit (and reads its result), and a later one waits
    -- for this transaction; its propagation trigger then sees this location row.
    SELECT i.region_id, i.station_id, i.access_classification, r.analysis_crs
      INTO NEW.region_id, NEW.station_id, NEW.access_classification, v_crs
    FROM core.incident_record i LEFT JOIN core.organizational_unit r ON r.id = i.region_id
    WHERE i.id = NEW.incident_id
    FOR SHARE OF i;
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

-- 2. Operational records -----------------------------------------------------------------------------
CREATE FUNCTION core.session_user_id() RETURNS uuid LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;
GRANT EXECUTE ON FUNCTION core.session_user_id() TO crimemap_app, crimemap_worker;

CREATE FUNCTION core.check_operational_record_insert() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
BEGIN
    IF NEW.review_status IS DISTINCT FROM (CASE WHEN NEW.record_kind IN
            ('LOCATION_CORRECTION_REQUEST', 'APPROVED_EVENT_MARKER', 'CIVIC_CONTEXT_REFERENCE')
            THEN 'PENDING' ELSE 'NOT_REQUIRED' END)
       OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL OR NEW.review_note IS NOT NULL THEN
        RAISE EXCEPTION 'a new operational record starts unreviewed (PENDING or NOT_REQUIRED by kind)'
            USING ERRCODE = '23514';
    END IF;
    -- Runs as the caller, so row-level security decides whether the incident is visible.
    IF NEW.subject_incident_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM core.incident_record i
        WHERE i.id = NEW.subject_incident_id AND i.region_id = NEW.region_id) THEN
        RAISE EXCEPTION 'the referenced record is not visible in this region' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER operational_record_insert_rules BEFORE INSERT ON core.operational_record
FOR EACH ROW EXECUTE FUNCTION core.check_operational_record_insert();

CREATE OR REPLACE FUNCTION core.reject_operational_record_change() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operational records cannot be deleted';
    END IF;
    IF OLD.review_status <> 'PENDING' THEN
        RAISE EXCEPTION 'operational record % has already been decided', OLD.id USING ERRCODE = '55000';
    END IF;
    IF NEW.review_status NOT IN ('APPROVED', 'REJECTED') THEN
        RAISE EXCEPTION 'a pending record can only be approved or rejected' USING ERRCODE = '23514';
    END IF;
    IF current_user = 'crimemap_app' AND NEW.reviewed_by IS DISTINCT FROM core.session_user_id() THEN
        RAISE EXCEPTION 'the reviewer is the session user' USING ERRCODE = '42501';
    END IF;
    NEW.reviewed_at := now();
    RETURN NEW;
END $$;

ALTER TABLE core.operational_record ADD CONSTRAINT operational_record_decision_has_reviewer
    CHECK ((review_status IN ('APPROVED', 'REJECTED')) = (reviewed_by IS NOT NULL));

-- Restrictive policies are AND-ed with the territory policy.
CREATE POLICY operational_record_author ON core.operational_record AS RESTRICTIVE FOR INSERT
    TO crimemap_app WITH CHECK (created_by = core.session_user_id());
CREATE POLICY analyst_note_author ON core.analyst_note AS RESTRICTIVE FOR INSERT
    TO crimemap_app WITH CHECK (created_by = core.session_user_id());

-- 3. User-flagged data-quality issues ---------------------------------------------------------------
CREATE POLICY dq_app_user_flag ON core.data_quality_issue AS RESTRICTIVE FOR INSERT TO crimemap_app
WITH CHECK (
    detected_by = 'USER' AND rule_code LIKE 'USER-%' AND subject_type = 'INCIDENT'
    AND incident_id IS NOT NULL AND subject_id = incident_id
    AND region_id = (SELECT i.region_id FROM core.incident_record i WHERE i.id = incident_id)
);
"""
    )


def downgrade() -> None:
    run_sql(
        """
DROP POLICY dq_app_user_flag ON core.data_quality_issue;
DROP POLICY analyst_note_author ON core.analyst_note;
DROP POLICY operational_record_author ON core.operational_record;
ALTER TABLE core.operational_record DROP CONSTRAINT operational_record_decision_has_reviewer;
DROP TRIGGER operational_record_insert_rules ON core.operational_record;
DROP FUNCTION core.check_operational_record_insert();
CREATE OR REPLACE FUNCTION core.reject_operational_record_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operational records cannot be deleted';
    END IF;
    IF OLD.review_status <> 'PENDING' THEN
        RAISE EXCEPTION 'operational record % has already been decided', OLD.id USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
DROP FUNCTION core.session_user_id();
CREATE OR REPLACE FUNCTION core.sync_incident_location() RETURNS trigger LANGUAGE plpgsql
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
        v_crs := CASE WHEN ST_X(NEW.geometry) < 78 THEN 32643 ELSE 32644 END;
    END IF;
    v_p := ST_Transform(NEW.geometry, v_crs);
    NEW.analysis_crs := v_crs; NEW.analysis_x := ST_X(v_p); NEW.analysis_y := ST_Y(v_p);
    RETURN NEW;
END $$;
"""
    )
