"""Phase 7 (responsible AI assistance, ADR-037).

- ``core.model_registry``: one row per model/rule version with its model card (intended and
  prohibited uses, features, parameters, evaluation, limitations, owner, approval) and a kill
  switch (``is_enabled``). A model can be switched on only when APPROVED, and a forecasting or
  language model only when every rollout gate is met (trigger).
- ``core.ai_run``: one detector run (the "input data snapshot"): parameters, input counts and
  exclusions, a hash of the inputs, and the outcome.
- ``core.ai_insight``: an insight card (spec "AI insight card contract"). It always has evidence, a
  baseline, limitations and an explanation (CHECKs). It is written only by the ``crimemap_ai`` role
  as PENDING, and changes only by an analyst's decision (VALIDATED or DISMISSED, with a note, by
  the session user). No person-level column exists: insights are about places, times and records.
- Role ``crimemap_ai`` (created by ``infrastructure/docker/postgres/init/02-ai-role.sh``): SELECT
  on the approved inputs, excluding highly restricted and sensitive-category records (row-level
  security), and INSERT on runs and insights. It has no grant on actions, exports, notes or case
  tables' writes — the database enforces "AI cannot create an action, export data or modify a case".
- Feature flags: ``ai_assistance`` (global AI kill switch, on), ``ai_copilot`` (off) and
  ``ai_forecasting`` (off).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13
"""

from crimemap_api.migration_sql import run_sql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crimemap_ai') THEN
        RAISE EXCEPTION 'role crimemap_ai is missing: run infrastructure/docker/postgres/init/02-ai-role.sh as the superuser first';
    END IF;
END $$;
GRANT USAGE ON SCHEMA core, audit TO crimemap_ai;

INSERT INTO core.feature_flag (key, enabled) VALUES ('ai_assistance', true), ('ai_copilot', false), ('ai_forecasting', false)
ON CONFLICT (key) DO NOTHING;
-- The AI switches are changed through the API (ai.config, fresh sign-in, audited).
GRANT UPDATE (enabled, changed_by, changed_at) ON core.feature_flag TO crimemap_app;

-- 1. Model registry ---------------------------------------------------------------------------------
CREATE TABLE core.model_registry (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                   text NOT NULL CHECK (code ~ '^[a-z][a-z0-9-]{2,40}$'),
    version                text NOT NULL CHECK (version ~ '^\\d+\\.\\d+\\.\\d+$'),
    name                   text NOT NULL,
    kind                   text NOT NULL CHECK (kind IN ('RULE', 'STATISTICAL', 'TEMPLATE', 'ML', 'LLM')),
    insight_type           text CHECK (insight_type IN ('DQ_ANOMALY', 'DUPLICATE', 'GEOCODE', 'EMERGING_PATTERN', 'CLUSTER')),
    description            text NOT NULL,
    intended_use           text NOT NULL,
    prohibited_uses        text[] NOT NULL CHECK (cardinality(prohibited_uses) >= 1),
    features               text[] NOT NULL,
    parameters             jsonb NOT NULL DEFAULT '{}'::jsonb,
    training_data_period   text,
    evaluation_metrics     jsonb NOT NULL DEFAULT '{}'::jsonb,
    bias_fairness_notes    text NOT NULL,
    limitations            text[] NOT NULL CHECK (cardinality(limitations) >= 1),
    owner                  text NOT NULL,
    approval_status        text NOT NULL CHECK (approval_status IN ('DRAFT', 'APPROVED', 'RETIRED')),
    approval_note          text,
    rollout_gates          jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_enabled             boolean NOT NULL DEFAULT false,
    rollback_to_version_id uuid REFERENCES core.model_registry (id),
    changed_by             uuid REFERENCES core.app_user (id),
    changed_at             timestamptz,
    change_reason          text CHECK (change_reason IS NULL OR length(change_reason) BETWEEN 10 AND 500),
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (code, version),
    CHECK (NOT is_enabled OR approval_status = 'APPROVED')
);

CREATE FUNCTION core.check_model_switch() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
    gate record;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'model registry entries are retired, not deleted';
    END IF;
    IF current_user <> 'crimemap_app' THEN
        RETURN NEW;
    END IF;
    -- The application may only flip the switch (with who, when and why); the model card is fixed.
    IF (NEW.code, NEW.version, NEW.name, NEW.kind, NEW.insight_type, NEW.description, NEW.intended_use,
        NEW.prohibited_uses, NEW.features, NEW.parameters, NEW.evaluation_metrics, NEW.limitations, NEW.owner,
        NEW.approval_status, NEW.approval_note, NEW.rollout_gates, NEW.rollback_to_version_id)
       IS DISTINCT FROM
       (OLD.code, OLD.version, OLD.name, OLD.kind, OLD.insight_type, OLD.description, OLD.intended_use,
        OLD.prohibited_uses, OLD.features, OLD.parameters, OLD.evaluation_metrics, OLD.limitations, OLD.owner,
        OLD.approval_status, OLD.approval_note, OLD.rollout_gates, OLD.rollback_to_version_id) THEN
        RAISE EXCEPTION 'only the kill switch can be changed here' USING ERRCODE = '42501';
    END IF;
    IF me IS NULL OR NEW.changed_by IS DISTINCT FROM me OR NEW.change_reason IS NULL THEN
        RAISE EXCEPTION 'the switch is changed by the session user, with a reason' USING ERRCODE = '42501';
    END IF;
    IF NEW.is_enabled AND NOT OLD.is_enabled THEN
        IF NEW.approval_status <> 'APPROVED' THEN
            RAISE EXCEPTION 'only an approved model can be switched on' USING ERRCODE = '55000';
        END IF;
        IF NEW.kind IN ('ML', 'LLM') THEN
            FOR gate IN SELECT key, value FROM jsonb_each(NEW.rollout_gates) LOOP
                IF gate.value IS DISTINCT FROM 'true'::jsonb THEN
                    RAISE EXCEPTION 'rollout gate not met: %', gate.key USING ERRCODE = '55000';
                END IF;
            END LOOP;
            IF NEW.rollout_gates = '{}'::jsonb THEN
                RAISE EXCEPTION 'rollout gates are not defined' USING ERRCODE = '55000';
            END IF;
        END IF;
    END IF;
    NEW.changed_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER model_registry_switch BEFORE UPDATE OR DELETE ON core.model_registry
FOR EACH ROW EXECUTE FUNCTION core.check_model_switch();
GRANT SELECT ON core.model_registry TO crimemap_app, crimemap_ai;
GRANT UPDATE (is_enabled, changed_by, changed_at, change_reason) ON core.model_registry TO crimemap_app;

-- Gates of the forecast model (all false: forecasting stays disabled until each is evidenced).
INSERT INTO core.model_registry (code, version, name, kind, insight_type, description, intended_use, prohibited_uses,
    features, parameters, training_data_period, evaluation_metrics, bias_fairness_notes, limitations, owner,
    approval_status, approval_note, rollout_gates, is_enabled) VALUES
('dq-anomaly', '1.0.0', 'Data-quality anomaly rules', 'STATISTICAL', 'DQ_ANOMALY',
 'Flags stations whose share of records without a usable location is far above their own baseline, and sources whose daily volume falls far below theirs.',
 'Point data stewards to feeds and stations whose data quality changed, for review at source.',
 ARRAY['Judging the performance of officers or stations', 'Any decision about a person'],
 ARRAY['records per station and source', 'share without usable location', 'daily volume'],
 '{"window_days": 28, "baseline_days": 84, "min_records": 20, "z_threshold": 3, "volume_window_days": 7}', 'rolling, from the data',
 '{"note": "Rule-based thresholds; not evaluated against labelled outcomes."}',
 'Stations with few records produce unstable shares; a minimum count applies. No personal data is used.',
 ARRAY['Shares are compared with the same station''s own past; a sustained change becomes the new baseline.', 'Synthetic demo data only.'],
 'Data Quality lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('duplicate-suggest', '1.0.0', 'Possible duplicate suggestions', 'RULE', 'DUPLICATE',
 'Scores pairs of records close in place and time, in the same category, from different sources or reports, by distance, time gap, locality and reporting station.',
 'Suggest records a data steward should compare. Never merges records.',
 ARRAY['Merging or deleting records automatically', 'Any inference about a person'],
 ARRAY['distance', 'time gap', 'category', 'locality text', 'reporting station'],
 '{"max_distance_m": 100, "max_gap_minutes": 120, "min_score": 0.7, "max_suggestions": 200}', 'n/a (rule)',
 '{"note": "Weights set by hand; precision not measured on labelled pairs."}',
 'Dense urban areas produce more candidate pairs; a minimum score applies. No personal data is used.',
 ARRAY['Two genuine incidents close together will be suggested; the steward decides.', 'Same-source pairs already flagged by rule DQ-DUP-001 are skipped.'],
 'Data Quality lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('geocode-suggest', '1.0.0', 'Geocoding suggestions', 'STATISTICAL', 'GEOCODE',
 'For a record without a reliable location, suggests the centre of well-located records with the same locality text in the same station, with a confidence from their number and spread.',
 'Help a data steward correct a location. The suggestion never changes the record.',
 ARRAY['Locating a residence or a person', 'Changing a record without review'],
 ARRAY['locality text', 'positions of well-located records in the same station and locality'],
 '{"min_support": 5, "support_confidence": 0.8, "high_support": 20, "high_spread_m": 300, "max_spread_m": 1500}', 'all well-located records',
 '{"note": "Spread is a proxy for accuracy; not checked against surveyed positions."}',
 'Localities with few records are less reliable and flagged as low confidence. Only locality text is used, never addresses of people.',
 ARRAY['A locality centre, not the exact place; mandatory review when confidence is low.', 'Locality spelling variants are not merged.'],
 'Data Quality lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('emerging-pattern', '1.0.0', 'Emerging place-time patterns', 'STATISTICAL', 'EMERGING_PATTERN',
 'Compares the last 28 days of recorded cases per 1 km cell and category with the same cell''s previous 24 weeks, using a Poisson upper-tail test.',
 'Prioritise a place and time window for analyst review. Never a person.',
 ARRAY['Predicting offenders or victims', 'Individual risk scores', 'Automatic deployment or enforcement', 'Public maps'],
 ARRAY['case counts per cell, category and 28-day window'],
 '{"cell_m": 1000, "window_days": 28, "baseline_windows": 6, "min_count": 5, "max_p": 0.001}', 'previous 168 days',
 '{"note": "p-values assume Poisson counts; overdispersion and multiple testing are only partly controlled (strict threshold)."}',
 'Places with more reporting or patrol presence record more incidents; a rise can reflect recording, not occurrence. Sensitive categories are excluded.',
 ARRAY['Recorded cases only; reporting changes look like rises.', 'Small counts are excluded (minimum 5).', 'Many cells are tested; some alerts are chance.'],
 'Crime analysis lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('cluster-explain', '1.0.0', 'Explainable clusters', 'STATISTICAL', 'CLUSTER',
 'Finds dense groups of recorded cases in the last 28 days per category with DBSCAN (250 m, at least 8 cases) and describes each by size, centre, extent, time span and hours.',
 'Describe where recent cases concentrate, for analyst review.',
 ARRAY['Linking cases to a person or group of people', 'Automatic deployment'],
 ARRAY['projected positions of recorded cases', 'occurrence time', 'category'],
 '{"eps_m": 250, "min_points": 8, "window_days": 28, "snap_m": 250}', 'last 28 days',
 '{"note": "Unsupervised; cluster validity not measured."}',
 'Clusters follow where incidents are recorded; centres are snapped to 250 m so no exact location is shown. Sensitive categories are excluded.',
 ARRAY['Parameters change which clusters appear.', 'A cluster is a concentration, not a series or a cause.'],
 'Crime analysis lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('briefing-template', '1.0.0', 'Template briefings', 'TEMPLATE', NULL,
 'Fills a fixed briefing template from insights an analyst has validated, citing each insight and its input run.',
 'Draft a briefing an analyst edits and owns.',
 ARRAY['Text about people', 'Briefings from unvalidated insights'],
 ARRAY['validated insights (evidence, limitations, citations)'], '{}', 'n/a',
 '{"note": "Deterministic template; no generation model."}',
 'Inherits the limitations of the cited insights.', ARRAY['A draft: the analyst is responsible for the final text.'],
 'Crime analysis lead (demo)', 'APPROVED', 'Demo approval for synthetic data only; not a production approval.', '{}', true),
('forecast-review-priority', '0.1.0', 'Short-horizon place-time review priority', 'ML', NULL,
 'Planned: forecast next-week counts per cell to prioritise review. Not built.',
 'Place-and-time review prioritisation only, after the rollout gates are met.',
 ARRAY['Predicting people', 'Individual risk scores', 'Automatic deployment', 'Public prediction maps'],
 ARRAY['(not defined)'], '{}', NULL, '{}',
 'Not assessed: the model does not exist.', ARRAY['Not built; disabled.'],
 'Accountable owner not yet assigned', 'DRAFT', 'Spec AI rollout gates: none met.',
 '{"data_quality_threshold_met": false, "baseline_model_exists": false, "beats_baseline_on_holdout": false, "error_and_geographic_concentration_reviewed": false, "analyst_acceptance_tested": false, "model_card_approved_by_owner": false, "security_privacy_review_passed": false, "rollback_tested": false}',
 false),
('copilot-llm', '0.0.0', 'Analyst copilot (language model adapter)', 'LLM', NULL,
 'Optional adapter for an approved language model over authorised, redacted dashboard data with citations. No model is configured.',
 'Summaries of selected authorised dashboard data, with citations.',
 ARRAY['Tool use that writes actions, records, exports or approvals', 'Questions about people'],
 ARRAY['(no model configured)'], '{}', NULL, '{}',
 'Not assessed: no model is configured.', ARRAY['Disabled; no approved deployment.'],
 'Accountable owner not yet assigned', 'DRAFT', 'Spec LLM restrictions; no approved deployment.',
 '{"approved_deployment": false, "authorization_filter_tested": false, "redaction_tested": false, "refusal_tested": false, "security_privacy_review_passed": false}',
 false);

-- 2. Runs (input data snapshots) -----------------------------------------------------------------
CREATE TABLE core.ai_run (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id         uuid NOT NULL REFERENCES core.model_registry (id),
    region_id        uuid NOT NULL REFERENCES core.organizational_unit (id),
    as_of            date NOT NULL,
    parameters       jsonb NOT NULL,
    input_summary    jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_sha256     text CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'),
    status           text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')),
    reason           text,
    insight_count    integer NOT NULL DEFAULT 0,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz
);
CREATE INDEX ai_run_region_idx ON core.ai_run (region_id, started_at);
ALTER TABLE core.ai_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.ai_run FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_run_app_read ON core.ai_run FOR SELECT TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR EXISTS (
    SELECT 1 FROM core.organizational_unit s WHERE s.parent_id = ai_run.region_id
    AND s.id = ANY (core.scope_ids('app.station_ids'))));
CREATE POLICY ai_run_ai ON core.ai_run TO crimemap_ai USING (true) WITH CHECK (true);
CREATE POLICY ai_run_owner ON core.ai_run TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT ON core.ai_run TO crimemap_app;
GRANT SELECT, INSERT, UPDATE (input_summary, input_sha256, status, reason, insight_count, finished_at) ON core.ai_run TO crimemap_ai;

-- 3. Insights ------------------------------------------------------------------------------------
CREATE TABLE core.ai_insight (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                   uuid NOT NULL REFERENCES core.ai_run (id),
    model_id                 uuid NOT NULL REFERENCES core.model_registry (id),
    insight_type             text NOT NULL CHECK (insight_type IN ('DQ_ANOMALY', 'DUPLICATE', 'GEOCODE', 'EMERGING_PATTERN', 'CLUSTER')),
    dedup_key                text NOT NULL CHECK (length(dedup_key) <= 200),
    region_id                uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id               uuid REFERENCES core.organizational_unit (id),
    geography                jsonb NOT NULL,
    geometry                 geometry(Geometry, 4326),
    window_start             date NOT NULL,
    window_end               date NOT NULL,
    analytical_category_id   uuid REFERENCES core.analytical_category (id),
    category_label           text NOT NULL,
    evidence                 jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'object' AND evidence <> '{}'::jsonb),
    baseline                 jsonb NOT NULL CHECK (jsonb_typeof(baseline) = 'object' AND baseline <> '{}'::jsonb),
    data_quality_score       numeric(4, 3) NOT NULL CHECK (data_quality_score BETWEEN 0 AND 1),
    exclusions               jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence               numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confidence_label         text NOT NULL CHECK (confidence_label IN ('LOW', 'MEDIUM', 'HIGH')),
    calibration_note         text NOT NULL CHECK (length(calibration_note) >= 10),
    explanation              text NOT NULL CHECK (length(explanation) BETWEEN 20 AND 2000),
    limitations              text[] NOT NULL CHECK (cardinality(limitations) >= 1),
    alternative_explanations text[] NOT NULL CHECK (cardinality(alternative_explanations) >= 1),
    subject_incident_ids     uuid[] NOT NULL DEFAULT '{}' CHECK (cardinality(subject_incident_ids) <= 2),
    review_required          text NOT NULL DEFAULT 'Analyst review required' CHECK (review_required = 'Analyst review required'),
    state                    text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'VALIDATED', 'DISMISSED')),
    decision_note            text CHECK (decision_note IS NULL OR length(decision_note) BETWEEN 10 AND 2000),
    decided_by               uuid REFERENCES core.app_user (id),
    decided_at               timestamptz,
    created_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_id, dedup_key),
    CHECK (window_start <= window_end),
    CHECK ((state = 'PENDING') = (decided_by IS NULL)),
    CHECK (state = 'PENDING' OR (decision_note IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX ai_insight_queue_idx ON core.ai_insight (region_id, state, created_at);

CREATE FUNCTION core.check_ai_insight() RETURNS trigger LANGUAGE plpgsql
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
        RETURN NEW;
    END IF;
    -- Only the review fields change, once, by an analyst (the session user).
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
CREATE TRIGGER ai_insight_rules BEFORE INSERT OR UPDATE OR DELETE ON core.ai_insight
FOR EACH ROW EXECUTE FUNCTION core.check_ai_insight();

ALTER TABLE core.ai_insight ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.ai_insight FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_insight_app_scope ON core.ai_insight FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY ai_insight_ai ON core.ai_insight FOR ALL TO crimemap_ai USING (true) WITH CHECK (true);
CREATE POLICY ai_insight_owner ON core.ai_insight TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT ON core.ai_insight TO crimemap_app;
GRANT UPDATE (state, decision_note, decided_by, decided_at) ON core.ai_insight TO crimemap_app;
GRANT SELECT, INSERT ON core.ai_insight TO crimemap_ai;

-- 4. What the AI role may read (approved inputs only) ---------------------------------------------
GRANT SELECT (id, source_system_id, record_type, analytical_category_id, occurrence_start_at, reported_at,
              reporting_station_id, region_id, station_id, beat_id, access_classification, is_sensitive_category,
              record_quality_status, is_demo)
      ON core.incident_record TO crimemap_ai;
GRANT SELECT (incident_id, geometry, locality, precision_level, confidence_score, geocode_method,
              analysis_crs, analysis_x, analysis_y, region_id, station_id, access_classification)
      ON core.incident_location TO crimemap_ai;
GRANT SELECT (id, subject_type, subject_id, incident_id, issue_type, rule_code, state, detail) ON core.data_quality_issue TO crimemap_ai;
GRANT SELECT ON core.analytical_category, core.organizational_unit, core.source_system, core.feature_flag TO crimemap_ai;
GRANT EXECUTE ON FUNCTION core.hex_cell(double precision, double precision, double precision),
                          core.hex_polygon(integer, integer, double precision, integer) TO crimemap_ai;
-- Highly restricted and sensitive-category records never reach a detector.
CREATE POLICY incident_ai ON core.incident_record FOR SELECT TO crimemap_ai
USING (access_classification <> 'HIGHLY_RESTRICTED' AND NOT is_sensitive_category);
CREATE POLICY incident_location_ai ON core.incident_location FOR SELECT TO crimemap_ai
USING (access_classification <> 'HIGHLY_RESTRICTED');
CREATE POLICY dq_ai ON core.data_quality_issue FOR SELECT TO crimemap_ai USING (true);

-- 5. Notes on insights ("Add Note") ----------------------------------------------------------------
ALTER TABLE core.analyst_note DROP CONSTRAINT analyst_note_subject_type_check;
ALTER TABLE core.analyst_note ADD CONSTRAINT analyst_note_subject_type_check CHECK (subject_type = ANY (ARRAY[
    'INCIDENT', 'CELL', 'PATTERN_REVIEW', 'ACTION', 'DQ_ISSUE', 'EVIDENCE_SNAPSHOT', 'AI_INSIGHT']));
"""
    )


def downgrade() -> None:
    run_sql(
        """
DELETE FROM core.analyst_note WHERE subject_type = 'AI_INSIGHT';
ALTER TABLE core.analyst_note DROP CONSTRAINT analyst_note_subject_type_check;
ALTER TABLE core.analyst_note ADD CONSTRAINT analyst_note_subject_type_check CHECK (subject_type = ANY (ARRAY[
    'INCIDENT', 'CELL', 'PATTERN_REVIEW', 'ACTION', 'DQ_ISSUE', 'EVIDENCE_SNAPSHOT']));
DROP POLICY dq_ai ON core.data_quality_issue;
DROP POLICY incident_location_ai ON core.incident_location;
DROP POLICY incident_ai ON core.incident_record;
REVOKE ALL ON core.incident_record, core.incident_location, core.data_quality_issue, core.analytical_category,
              core.organizational_unit, core.source_system, core.feature_flag FROM crimemap_ai;
REVOKE EXECUTE ON FUNCTION core.hex_cell(double precision, double precision, double precision),
                           core.hex_polygon(integer, integer, double precision, integer) FROM crimemap_ai;
DROP TABLE core.ai_insight;
DROP FUNCTION core.check_ai_insight();
DROP TABLE core.ai_run;
DROP TABLE core.model_registry;
DROP FUNCTION core.check_model_switch();
DELETE FROM core.feature_flag WHERE key IN ('ai_assistance', 'ai_copilot', 'ai_forecasting');
REVOKE UPDATE ON core.feature_flag FROM crimemap_app;
REVOKE USAGE ON SCHEMA core, audit FROM crimemap_ai;
"""
    )
