"""Phase 6 (review, action and outcome workflow).

Every state transition is enforced twice: by the API (permission, territory, separation of duties)
and here, by triggers that allow only the defined transitions, by the session user, with the
required fields (ADR-036). The append-only ``core.workflow_transition`` is each item's history.

- Pattern review: DRAFT -> (submit, author) SUBMITTED/PENDING -> (decide, a different person)
  APPROVED | REJECTED, or RETURNED (back to DRAFT for the author to revise and resubmit). The
  evidence (cells, periods, counts, snapshot) never changes after creation.
- Operational action: proposed from an APPROVED pattern review; PROPOSED -> APPROVED | REJECTED by
  someone other than the proposer; NOT_STARTED -> COMPLETED with completion evidence, only once
  approved. Outcome review due 28 days after the action ends.
- Outcome review: for a COMPLETED action; before/after metrics are computed by the server and fixed;
  DRAFT -> FINAL only with a baseline, at least one evidence reference and a conclusion; FINAL is
  immutable.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
-- 1. Pattern review decisions ------------------------------------------------------------------------
ALTER TABLE core.pattern_review
    ADD COLUMN analyst_assessment text CHECK (analyst_assessment IS NULL OR length(analyst_assessment) BETWEEN 10 AND 4000),
    ADD COLUMN submitted_at timestamptz,
    ADD COLUMN decision_rationale text CHECK (decision_rationale IS NULL OR length(decision_rationale) BETWEEN 10 AND 4000),
    ADD COLUMN decided_by uuid REFERENCES core.app_user (id),
    ADD COLUMN decided_at timestamptz,
    ADD CONSTRAINT pattern_review_decider_differs CHECK (decided_by IS NULL OR decided_by <> created_by),
    ADD CONSTRAINT pattern_review_states CHECK (
        (analyst_status = 'DRAFT' AND supervisor_status IS NULL)
     OR (analyst_status = 'DRAFT' AND supervisor_status = 'RETURNED')
     OR (analyst_status = 'SUBMITTED' AND supervisor_status IN ('PENDING', 'APPROVED', 'REJECTED')));

CREATE FUNCTION core.check_pattern_review_change() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pattern reviews cannot be deleted';
    END IF;
    -- The evidence is fixed once the review exists.
    IF (NEW.review_type, NEW.grid_cell_ids, NEW.analytical_category_id, NEW.category_label, NEW.period_start,
        NEW.period_end, NEW.baseline_start, NEW.baseline_end, NEW.record_count, NEW.baseline_count,
        NEW.evidence_snapshot_id, NEW.region_id, NEW.station_id, NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.review_type, OLD.grid_cell_ids, OLD.analytical_category_id, OLD.category_label, OLD.period_start,
        OLD.period_end, OLD.baseline_start, OLD.baseline_end, OLD.record_count, OLD.baseline_count,
        OLD.evidence_snapshot_id, OLD.region_id, OLD.station_id, OLD.created_by, OLD.created_at)
       OR NOT ST_Equals(coalesce(NEW.area_geometry, 'POINT EMPTY'::geometry), coalesce(OLD.area_geometry, 'POINT EMPTY'::geometry))
       OR NEW.quality_summary IS DISTINCT FROM OLD.quality_summary THEN
        RAISE EXCEPTION 'the evidence of a pattern review cannot change' USING ERRCODE = '23514';
    END IF;
    IF current_user <> 'crimemap_app' THEN
        RETURN NEW;
    END IF;
    IF OLD.analyst_status = 'DRAFT' AND NEW.analyst_status = 'DRAFT' THEN
        -- revision by the author (also after a return)
        IF me IS DISTINCT FROM OLD.created_by OR NEW.supervisor_status IS DISTINCT FROM OLD.supervisor_status THEN
            RAISE EXCEPTION 'only the author may revise a draft' USING ERRCODE = '42501';
        END IF;
    ELSIF OLD.analyst_status = 'DRAFT' AND NEW.analyst_status = 'SUBMITTED' THEN
        IF me IS DISTINCT FROM OLD.created_by OR NEW.supervisor_status <> 'PENDING' OR NEW.analyst_assessment IS NULL THEN
            RAISE EXCEPTION 'only the author submits, with an assessment' USING ERRCODE = '42501';
        END IF;
        NEW.submitted_at := now();
        NEW.decided_by := NULL; NEW.decided_at := NULL; NEW.decision_rationale := NULL;
    ELSIF OLD.supervisor_status = 'PENDING' AND NEW.supervisor_status IN ('APPROVED', 'REJECTED', 'RETURNED') THEN
        IF me IS NULL OR me = OLD.created_by OR NEW.decided_by IS DISTINCT FROM me OR NEW.decision_rationale IS NULL THEN
            RAISE EXCEPTION 'a different person decides, with a rationale' USING ERRCODE = '42501';
        END IF;
        IF NEW.analyst_narrative IS DISTINCT FROM OLD.analyst_narrative
           OR NEW.recommended_next_step IS DISTINCT FROM OLD.recommended_next_step
           OR NEW.analyst_assessment IS DISTINCT FROM OLD.analyst_assessment THEN
            RAISE EXCEPTION 'a decision does not change the analyst''s text' USING ERRCODE = '23514';
        END IF;
        NEW.decided_at := now();
    ELSE
        RAISE EXCEPTION 'pattern review transition not allowed' USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER pattern_review_transitions BEFORE UPDATE OR DELETE ON core.pattern_review
FOR EACH ROW EXECUTE FUNCTION core.check_pattern_review_change();
GRANT UPDATE (analyst_narrative, recommended_next_step, analyst_assessment, analyst_status, supervisor_status,
              decision_rationale, decided_by, decided_at, submitted_at, updated_at)
      ON core.pattern_review TO crimemap_app;

-- 2. Operational actions ----------------------------------------------------------------------------
CREATE TABLE core.operational_action (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_review_id     uuid NOT NULL REFERENCES core.pattern_review (id),
    action_type           text NOT NULL CHECK (action_type IN ('PATROL_VISIBILITY','COMMUNITY_ENGAGEMENT','JOINT_INSPECTION',
                                                               'ENVIRONMENTAL_REQUEST','DATA_QUALITY_FIX','OTHER')),
    description           text NOT NULL CHECK (length(description) BETWEEN 20 AND 2000),
    grid_cell_ids         text[] NOT NULL CHECK (cardinality(grid_cell_ids) BETWEEN 1 AND 200),
    territory_geometry    geometry(MultiPolygon, 4326),
    region_id             uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id            uuid NOT NULL REFERENCES core.organizational_unit (id),
    assignee_id           uuid REFERENCES core.app_user (id),
    start_at              date NOT NULL,
    end_at                date NOT NULL,
    approval_status       text NOT NULL DEFAULT 'PROPOSED' CHECK (approval_status IN ('PROPOSED','APPROVED','REJECTED')),
    approval_rationale    text CHECK (approval_rationale IS NULL OR length(approval_rationale) BETWEEN 10 AND 2000),
    proposed_by           uuid NOT NULL REFERENCES core.app_user (id),
    approved_by           uuid REFERENCES core.app_user (id),
    approved_at           timestamptz,
    completion_status     text NOT NULL DEFAULT 'NOT_STARTED' CHECK (completion_status IN ('NOT_STARTED','COMPLETED')),
    completion_evidence   text CHECK (completion_evidence IS NULL OR length(completion_evidence) BETWEEN 10 AND 2000),
    completion_reference  text CHECK (completion_reference IS NULL OR length(completion_reference) <= 500),
    completed_by          uuid REFERENCES core.app_user (id),
    completed_at          timestamptz,
    outcome_review_due_at date NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CHECK (end_at >= start_at AND end_at - start_at <= 366),
    CHECK (approved_by IS NULL OR approved_by <> proposed_by),
    CHECK ((approval_status = 'PROPOSED') = (approved_by IS NULL)),
    CHECK (completion_status = 'NOT_STARTED' OR (approval_status = 'APPROVED' AND completion_evidence IS NOT NULL
                                                  AND completed_by IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE INDEX operational_action_scope_idx ON core.operational_action (region_id, station_id, approval_status);

CREATE FUNCTION core.check_operational_action() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operational actions cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.approval_status <> 'PROPOSED' OR NEW.completion_status <> 'NOT_STARTED' OR NEW.approved_by IS NOT NULL THEN
            RAISE EXCEPTION 'a new action starts proposed and not started' USING ERRCODE = '23514';
        END IF;
        -- Runs as the caller: the pattern review must be visible, APPROVED and in the same region.
        IF NOT EXISTS (SELECT 1 FROM core.pattern_review p WHERE p.id = NEW.pattern_review_id
                       AND p.supervisor_status = 'APPROVED' AND p.region_id = NEW.region_id) THEN
            RAISE EXCEPTION 'actions follow an approved pattern review in the same region' USING ERRCODE = '23503';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.pattern_review_id, NEW.action_type, NEW.description, NEW.grid_cell_ids, NEW.region_id, NEW.station_id,
        NEW.start_at, NEW.end_at, NEW.proposed_by, NEW.outcome_review_due_at, NEW.created_at)
       IS DISTINCT FROM
       (OLD.pattern_review_id, OLD.action_type, OLD.description, OLD.grid_cell_ids, OLD.region_id, OLD.station_id,
        OLD.start_at, OLD.end_at, OLD.proposed_by, OLD.outcome_review_due_at, OLD.created_at) THEN
        RAISE EXCEPTION 'an action''s plan cannot change after it is proposed' USING ERRCODE = '23514';
    END IF;
    IF current_user <> 'crimemap_app' THEN
        RETURN NEW;
    END IF;
    IF OLD.approval_status = 'PROPOSED' AND NEW.approval_status IN ('APPROVED', 'REJECTED') THEN
        IF me IS NULL OR me = OLD.proposed_by OR NEW.approved_by IS DISTINCT FROM me OR NEW.approval_rationale IS NULL
           OR NEW.completion_status <> 'NOT_STARTED' THEN
            RAISE EXCEPTION 'a different person approves or rejects, with a rationale' USING ERRCODE = '42501';
        END IF;
        NEW.approved_at := now();
    ELSIF OLD.approval_status = 'APPROVED' AND NEW.approval_status = 'APPROVED'
          AND OLD.completion_status = 'NOT_STARTED' AND NEW.completion_status = 'COMPLETED' THEN
        IF NEW.completed_by IS DISTINCT FROM me OR NEW.assignee_id IS DISTINCT FROM OLD.assignee_id THEN
            RAISE EXCEPTION 'completion is recorded by the session user' USING ERRCODE = '42501';
        END IF;
        NEW.completed_at := now();
    ELSE
        RAISE EXCEPTION 'operational action transition not allowed' USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER operational_action_rules BEFORE INSERT OR UPDATE OR DELETE ON core.operational_action
FOR EACH ROW EXECUTE FUNCTION core.check_operational_action();

ALTER TABLE core.operational_action ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.operational_action FORCE ROW LEVEL SECURITY;
CREATE POLICY operational_action_app_scope ON core.operational_action FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY operational_action_author ON core.operational_action AS RESTRICTIVE FOR INSERT TO crimemap_app
WITH CHECK (proposed_by = core.session_user_id());
CREATE POLICY operational_action_owner ON core.operational_action TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (pattern_review_id, action_type, description, grid_cell_ids, territory_geometry, region_id,
                      station_id, assignee_id, start_at, end_at, proposed_by, outcome_review_due_at)
      ON core.operational_action TO crimemap_app;
GRANT UPDATE (approval_status, approval_rationale, approved_by, approved_at, assignee_id, completion_status,
              completion_evidence, completion_reference, completed_by, completed_at, updated_at)
      ON core.operational_action TO crimemap_app;

-- 3. Outcome reviews --------------------------------------------------------------------------------
CREATE TABLE core.outcome_review (
    id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id                    uuid NOT NULL UNIQUE REFERENCES core.operational_action (id),
    selected_measures            text[] NOT NULL CHECK (cardinality(selected_measures) >= 1),
    baseline_start               date NOT NULL,
    baseline_end                 date NOT NULL,
    after_start                  date NOT NULL,
    after_end                    date NOT NULL,
    comparison_kind              text NOT NULL CHECK (comparison_kind IN ('CELLS', 'STATION', 'NONE')),
    comparison_keys              text[] NOT NULL DEFAULT '{}',
    comparison_area_geometry     geometry(MultiPolygon, 4326),
    before_metrics               jsonb NOT NULL,
    after_metrics                jsonb NOT NULL,
    displacement_diffusion_notes text CHECK (displacement_diffusion_notes IS NULL OR length(displacement_diffusion_notes) <= 4000),
    evidence_references          jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(evidence_references) = 'array'),
    reviewer_conclusion          text CHECK (reviewer_conclusion IS NULL OR length(reviewer_conclusion) <= 4000),
    status                       text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'FINAL')),
    finalised_at                 timestamptz,
    region_id                    uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id                   uuid REFERENCES core.organizational_unit (id),
    created_by                   uuid NOT NULL REFERENCES core.app_user (id),
    created_at                   timestamptz NOT NULL DEFAULT now(),
    updated_at                   timestamptz NOT NULL DEFAULT now(),
    CHECK (baseline_start <= baseline_end AND baseline_end < after_start AND after_start <= after_end),
    -- Cannot be finalised without baseline, evidence references and a conclusion (spec acceptance).
    CHECK (status = 'DRAFT' OR (jsonb_array_length(evidence_references) >= 1
                                AND length(coalesce(reviewer_conclusion, '')) >= 20
                                AND finalised_at IS NOT NULL))
);

CREATE FUNCTION core.check_outcome_review() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'outcome reviews cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'DRAFT' THEN
            RAISE EXCEPTION 'a new outcome review is a draft' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM core.operational_action a WHERE a.id = NEW.action_id
                       AND a.completion_status = 'COMPLETED' AND a.region_id = NEW.region_id) THEN
            RAISE EXCEPTION 'outcome reviews follow a completed action' USING ERRCODE = '23503';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'FINAL' THEN
        RAISE EXCEPTION 'a final outcome review cannot change' USING ERRCODE = '55000';
    END IF;
    IF (NEW.action_id, NEW.selected_measures, NEW.baseline_start, NEW.baseline_end, NEW.after_start, NEW.after_end,
        NEW.comparison_kind, NEW.comparison_keys, NEW.before_metrics, NEW.after_metrics, NEW.region_id, NEW.station_id,
        NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.action_id, OLD.selected_measures, OLD.baseline_start, OLD.baseline_end, OLD.after_start, OLD.after_end,
        OLD.comparison_kind, OLD.comparison_keys, OLD.before_metrics, OLD.after_metrics, OLD.region_id, OLD.station_id,
        OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'measured values of an outcome review cannot change' USING ERRCODE = '23514';
    END IF;
    IF current_user = 'crimemap_app' AND me IS DISTINCT FROM OLD.created_by THEN
        RAISE EXCEPTION 'only the reviewer edits or finalises an outcome review' USING ERRCODE = '42501';
    END IF;
    IF NEW.status = 'FINAL' THEN
        NEW.finalised_at := now();
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
CREATE TRIGGER outcome_review_rules BEFORE INSERT OR UPDATE OR DELETE ON core.outcome_review
FOR EACH ROW EXECUTE FUNCTION core.check_outcome_review();

ALTER TABLE core.outcome_review ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.outcome_review FORCE ROW LEVEL SECURITY;
CREATE POLICY outcome_review_app_scope ON core.outcome_review FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')));
CREATE POLICY outcome_review_author ON core.outcome_review AS RESTRICTIVE FOR INSERT TO crimemap_app
WITH CHECK (created_by = core.session_user_id());
CREATE POLICY outcome_review_owner ON core.outcome_review TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (action_id, selected_measures, baseline_start, baseline_end, after_start, after_end,
                      comparison_kind, comparison_keys, comparison_area_geometry, before_metrics, after_metrics,
                      region_id, station_id, created_by)
      ON core.outcome_review TO crimemap_app;
GRANT UPDATE (displacement_diffusion_notes, evidence_references, reviewer_conclusion, status, finalised_at, updated_at)
      ON core.outcome_review TO crimemap_app;

-- 4. History (append-only) --------------------------------------------------------------------------
CREATE TABLE core.workflow_transition (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_type text NOT NULL CHECK (object_type IN ('PATTERN_REVIEW', 'ACTION', 'OUTCOME_REVIEW')),
    object_id   uuid NOT NULL,
    from_state  text,
    to_state    text NOT NULL,
    rationale   text CHECK (rationale IS NULL OR length(rationale) <= 4000),
    region_id   uuid NOT NULL REFERENCES core.organizational_unit (id),
    station_id  uuid REFERENCES core.organizational_unit (id),
    actor_id    uuid NOT NULL REFERENCES core.app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workflow_transition_object_idx ON core.workflow_transition (object_type, object_id, id);
ALTER TABLE core.workflow_transition ENABLE ROW LEVEL SECURITY;
ALTER TABLE core.workflow_transition FORCE ROW LEVEL SECURITY;
CREATE POLICY workflow_transition_app_scope ON core.workflow_transition FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
WITH CHECK ((region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids')))
            AND actor_id = core.session_user_id());
CREATE POLICY workflow_transition_owner ON core.workflow_transition TO crimemap_migrator USING (true) WITH CHECK (true);
GRANT SELECT, INSERT (object_type, object_id, from_state, to_state, rationale, region_id, station_id, actor_id)
      ON core.workflow_transition TO crimemap_app;
"""
    )


def downgrade() -> None:
    run_sql(
        """
DROP TABLE core.workflow_transition;
DROP TABLE core.outcome_review;
DROP FUNCTION core.check_outcome_review();
DROP TABLE core.operational_action;
DROP FUNCTION core.check_operational_action();
DROP TRIGGER pattern_review_transitions ON core.pattern_review;
DROP FUNCTION core.check_pattern_review_change();
REVOKE UPDATE ON core.pattern_review FROM crimemap_app;
ALTER TABLE core.pattern_review
    DROP CONSTRAINT pattern_review_states,
    DROP CONSTRAINT pattern_review_decider_differs,
    DROP COLUMN decided_at, DROP COLUMN decided_by, DROP COLUMN decision_rationale,
    DROP COLUMN submitted_at, DROP COLUMN analyst_assessment;
"""
    )
