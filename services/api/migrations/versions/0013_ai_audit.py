"""Phase 7 audit fixes (ADR-037, revised).

- The AI role sees a location or a data-quality issue only if it may see the incident (row-level
  security on incident_record then applies): sensitive-category and highly restricted incidents'
  locations and issues were visible to it.
- The AI role (now a LOGIN used only by the ai-worker container, see 02-ai-role.sh) appends its own
  audit events (AI_RUN), so that container needs no other credentials.
- Feature flags: the application role may change the AI flags only, as the session user (time set
  here), and may switch on forecasting or the copilot only while their model is enabled — in the
  database, not only in the API. (The 0012 grant had also opened civic_context to it.)
- Insights can be written only for an approved, enabled rule or statistical model of the same
  insight type, into a running run of that model and region; a run only for such a model.
- Insights readable by a global ``ai.insight.read`` grant (auditors): ``app.ai_global``.
- Required action text as in the spec: "Analyst review required."

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13
"""

from crimemap_api.migration_sql import run_sql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
-- 1. Inputs follow the incident -------------------------------------------------------------------
DROP POLICY incident_location_ai ON core.incident_location;
CREATE POLICY incident_location_ai ON core.incident_location FOR SELECT TO crimemap_ai
USING (access_classification IS DISTINCT FROM 'HIGHLY_RESTRICTED'
       AND EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = incident_location.incident_id));
DROP POLICY dq_ai ON core.data_quality_issue;
CREATE POLICY dq_ai ON core.data_quality_issue FOR SELECT TO crimemap_ai
USING (incident_id IS NULL OR EXISTS (SELECT 1 FROM core.incident_record r WHERE r.id = data_quality_issue.incident_id));

-- 2. The AI login writes its own audit events -------------------------------------------------------
GRANT INSERT (id, actor_user_id, actor_subject, actor_roles, action, object_type, object_id,
              request_id, ip_address, user_agent_hash, purpose, filters, result, reason,
              before_meta, after_meta, export_classification, environment)
      ON audit.audit_event TO crimemap_ai;

-- 3. Feature flags -------------------------------------------------------------------------------------
CREATE FUNCTION core.check_feature_flag() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
    model text;
BEGIN
    IF current_user <> 'crimemap_app' THEN
        RETURN NEW;
    END IF;
    IF NEW.key IS DISTINCT FROM OLD.key OR OLD.key NOT IN ('ai_assistance', 'ai_copilot', 'ai_forecasting') THEN
        RAISE EXCEPTION 'only the AI switches can be changed here' USING ERRCODE = '42501';
    END IF;
    IF me IS NULL OR NEW.changed_by IS DISTINCT FROM me THEN
        RAISE EXCEPTION 'a switch is changed by the session user' USING ERRCODE = '42501';
    END IF;
    IF NEW.enabled AND OLD.key IN ('ai_copilot', 'ai_forecasting') THEN
        model := CASE OLD.key WHEN 'ai_copilot' THEN 'copilot-llm' ELSE 'forecast-review-priority' END;
        IF NOT EXISTS (SELECT 1 FROM core.model_registry m WHERE m.code = model AND m.is_enabled) THEN
            RAISE EXCEPTION 'rollout gates not met: % is not enabled', model USING ERRCODE = '55000';
        END IF;
    END IF;
    NEW.changed_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER feature_flag_rules BEFORE UPDATE ON core.feature_flag
FOR EACH ROW EXECUTE FUNCTION core.check_feature_flag();

-- 4. Insights and runs only for an approved, enabled detector ------------------------------------
CREATE FUNCTION core.check_ai_run() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NOT EXISTS (
        SELECT 1 FROM core.model_registry m WHERE m.id = NEW.model_id AND m.approval_status = 'APPROVED'
        AND m.is_enabled AND m.kind IN ('RULE', 'STATISTICAL') AND m.insight_type IS NOT NULL) THEN
        RAISE EXCEPTION 'runs are only for approved, enabled detectors' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ai_run_rules BEFORE INSERT ON core.ai_run FOR EACH ROW EXECUTE FUNCTION core.check_ai_run();

CREATE OR REPLACE FUNCTION core.check_ai_insight() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'AI insights cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'PENDING' OR NEW.decided_by IS NOT NULL OR NEW.decision_note IS NOT NULL THEN
            RAISE EXCEPTION 'a new insight awaits analyst review' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM core.model_registry m JOIN core.ai_run r ON r.model_id = m.id
            WHERE m.id = NEW.model_id AND r.id = NEW.run_id AND r.region_id = NEW.region_id AND r.status = 'RUNNING'
              AND m.approval_status = 'APPROVED' AND m.is_enabled AND m.kind IN ('RULE', 'STATISTICAL')
              AND m.insight_type = NEW.insight_type) THEN
            RAISE EXCEPTION 'insights come from a running run of an approved, enabled detector of that type'
                USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.run_id, NEW.model_id, NEW.insight_type, NEW.dedup_key, NEW.region_id, NEW.station_id, NEW.geography,
        NEW.window_start, NEW.window_end, NEW.analytical_category_id, NEW.category_label, NEW.evidence, NEW.baseline,
        NEW.data_quality_score, NEW.exclusions, NEW.confidence, NEW.confidence_label, NEW.calibration_note,
        NEW.explanation, NEW.limitations, NEW.alternative_explanations, NEW.subject_incident_ids, NEW.created_at)
       IS DISTINCT FROM
       (OLD.run_id, OLD.model_id, OLD.insight_type, OLD.dedup_key, OLD.region_id, OLD.station_id, OLD.geography,
        OLD.window_start, OLD.window_end, OLD.analytical_category_id, OLD.category_label, OLD.evidence, OLD.baseline,
        OLD.data_quality_score, OLD.exclusions, OLD.confidence, OLD.confidence_label, OLD.calibration_note,
        OLD.explanation, OLD.limitations, OLD.alternative_explanations, OLD.subject_incident_ids, OLD.created_at)
       OR NOT ST_Equals(coalesce(NEW.geometry, 'POINT EMPTY'::geometry), coalesce(OLD.geometry, 'POINT EMPTY'::geometry)) THEN
        RAISE EXCEPTION 'an insight''s content cannot change' USING ERRCODE = '23514';
    END IF;
    IF OLD.state <> 'PENDING' THEN
        RAISE EXCEPTION 'this insight has already been reviewed' USING ERRCODE = '55000';
    END IF;
    IF current_user = 'crimemap_app' AND (me IS NULL OR NEW.decided_by IS DISTINCT FROM me) THEN
        RAISE EXCEPTION 'the review is recorded by the session user' USING ERRCODE = '42501';
    END IF;
    NEW.decided_at := now();
    RETURN NEW;
END $$;

-- 5. Global readers (auditors) -------------------------------------------------------------------
DROP POLICY ai_insight_app_scope ON core.ai_insight;
CREATE POLICY ai_insight_app_scope ON core.ai_insight FOR ALL TO crimemap_app
USING (current_setting('app.ai_global', true) = 'on'
       OR region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
DROP POLICY ai_run_app_read ON core.ai_run;
CREATE POLICY ai_run_app_read ON core.ai_run FOR SELECT TO crimemap_app
USING (current_setting('app.ai_global', true) = 'on'
       OR region_id = ANY (core.scope_ids('app.region_ids')) OR EXISTS (
    SELECT 1 FROM core.organizational_unit s WHERE s.parent_id = ai_run.region_id
    AND s.id = ANY (core.scope_ids('app.station_ids'))));

-- 6. Model cards: state the approved use precisely (spec AI use case 2) -----------------------------
UPDATE core.model_registry SET
    intended_use = 'Source-ingest validation: suggest records (including cases) a data steward should compare at source. Never merges or changes official cases.',
    limitations = limitations || ARRAY['Applies to ingested records for validation only; any correction is made at the source system.']
WHERE code = 'duplicate-suggest' AND version = '1.0.0';
UPDATE core.model_registry SET
    limitations = limitations || ARRAY['Uses locations with confidence 0.4 or more only (the same rule as the data-quality anomaly).']
WHERE code IN ('emerging-pattern', 'cluster-explain') AND version = '1.0.0';

-- 7. Spec wording ----------------------------------------------------------------------------------
ALTER TABLE core.ai_insight DROP CONSTRAINT ai_insight_review_required_check;
-- The fixed label is not content (CHECK-pinned); the owner relabels existing rows once.
ALTER TABLE core.ai_insight DISABLE TRIGGER ai_insight_rules;
UPDATE core.ai_insight SET review_required = 'Analyst review required.';
ALTER TABLE core.ai_insight ENABLE TRIGGER ai_insight_rules;
ALTER TABLE core.ai_insight ALTER COLUMN review_required SET DEFAULT 'Analyst review required.';
ALTER TABLE core.ai_insight ADD CONSTRAINT ai_insight_review_required_check
    CHECK (review_required = 'Analyst review required.');
"""
    )


def downgrade() -> None:
    run_sql(
        """
ALTER TABLE core.ai_insight DROP CONSTRAINT ai_insight_review_required_check;
ALTER TABLE core.ai_insight DISABLE TRIGGER ai_insight_rules;
UPDATE core.ai_insight SET review_required = 'Analyst review required';
ALTER TABLE core.ai_insight ENABLE TRIGGER ai_insight_rules;
ALTER TABLE core.ai_insight ALTER COLUMN review_required SET DEFAULT 'Analyst review required';
ALTER TABLE core.ai_insight ADD CONSTRAINT ai_insight_review_required_check CHECK (review_required = 'Analyst review required');
DROP POLICY ai_run_app_read ON core.ai_run;
CREATE POLICY ai_run_app_read ON core.ai_run FOR SELECT TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR EXISTS (
    SELECT 1 FROM core.organizational_unit s WHERE s.parent_id = ai_run.region_id
    AND s.id = ANY (core.scope_ids('app.station_ids'))));
DROP POLICY ai_insight_app_scope ON core.ai_insight;
CREATE POLICY ai_insight_app_scope ON core.ai_insight FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
DROP TRIGGER ai_run_rules ON core.ai_run;
DROP FUNCTION core.check_ai_run();
DROP TRIGGER feature_flag_rules ON core.feature_flag;
DROP FUNCTION core.check_feature_flag();
REVOKE INSERT ON audit.audit_event FROM crimemap_ai;
DROP POLICY dq_ai ON core.data_quality_issue;
CREATE POLICY dq_ai ON core.data_quality_issue FOR SELECT TO crimemap_ai USING (true);
DROP POLICY incident_location_ai ON core.incident_location;
CREATE POLICY incident_location_ai ON core.incident_location FOR SELECT TO crimemap_ai
USING (access_classification <> 'HIGHLY_RESTRICTED');
"""
    )
