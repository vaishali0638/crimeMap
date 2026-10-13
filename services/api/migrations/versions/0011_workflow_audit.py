"""Phase 6 audit fixes to the workflow rules (ADR-036, revised).

- Pattern review: a revision by the author can no longer change the supervisor's decision fields or
  the submission time; a decision cannot change the submission time.
- Operational action: approval cannot fill in completion fields; completion cannot rewrite the
  approval, needs the action to have started (IST), and is by someone other than the approver.
- Outcome review: the reviewer is not the person who completed the action; comparison keys are
  codes only, and none for "no comparison".
- History: ``core.workflow_transition`` is written only by these triggers (the application role
  can no longer insert, so history cannot be forged); revisions are recorded too.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13
"""

from crimemap_api.migration_sql import run_sql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

PATTERN_REVIEW = """
CREATE OR REPLACE FUNCTION core.check_pattern_review_change() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'pattern reviews cannot be deleted';
    END IF;
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
        -- revision by the author (also after a return); the last decision stays as it was
        IF me IS DISTINCT FROM OLD.created_by OR NEW.supervisor_status IS DISTINCT FROM OLD.supervisor_status
           OR (NEW.decision_rationale, NEW.decided_by, NEW.decided_at, NEW.submitted_at)
              IS DISTINCT FROM (OLD.decision_rationale, OLD.decided_by, OLD.decided_at, OLD.submitted_at) THEN
            RAISE EXCEPTION 'only the author may revise a draft, and not the decision' USING ERRCODE = '42501';
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
           OR NEW.analyst_assessment IS DISTINCT FROM OLD.analyst_assessment
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at THEN
            RAISE EXCEPTION 'a decision does not change the analyst''s text' USING ERRCODE = '23514';
        END IF;
        NEW.decided_at := now();
    ELSE
        RAISE EXCEPTION 'pattern review transition not allowed' USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
"""

OPERATIONAL_ACTION = """
CREATE OR REPLACE FUNCTION core.check_operational_action() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operational actions cannot be deleted';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.approval_status <> 'PROPOSED' OR NEW.completion_status <> 'NOT_STARTED' OR NEW.approved_by IS NOT NULL
           OR NEW.completed_by IS NOT NULL OR NEW.completion_evidence IS NOT NULL OR NEW.completion_reference IS NOT NULL THEN
            RAISE EXCEPTION 'a new action starts proposed and not started' USING ERRCODE = '23514';
        END IF;
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
           OR NEW.completion_status <> 'NOT_STARTED'
           OR (NEW.completion_evidence, NEW.completion_reference, NEW.completed_by, NEW.completed_at)
              IS DISTINCT FROM (OLD.completion_evidence, OLD.completion_reference, OLD.completed_by, OLD.completed_at) THEN
            RAISE EXCEPTION 'a different person approves or rejects, with a rationale' USING ERRCODE = '42501';
        END IF;
        NEW.approved_at := now();
    ELSIF OLD.approval_status = 'APPROVED' AND NEW.approval_status = 'APPROVED'
          AND OLD.completion_status = 'NOT_STARTED' AND NEW.completion_status = 'COMPLETED' THEN
        IF NEW.completed_by IS DISTINCT FROM me OR me = OLD.approved_by
           OR NEW.assignee_id IS DISTINCT FROM OLD.assignee_id
           OR (NEW.approved_by, NEW.approval_rationale, NEW.approved_at)
              IS DISTINCT FROM (OLD.approved_by, OLD.approval_rationale, OLD.approved_at) THEN
            RAISE EXCEPTION 'completion is recorded by the session user, not the approver' USING ERRCODE = '42501';
        END IF;
        IF (now() AT TIME ZONE 'Asia/Kolkata')::date < OLD.start_at THEN
            RAISE EXCEPTION 'an action cannot be completed before it starts' USING ERRCODE = '55000';
        END IF;
        NEW.completed_at := now();
    ELSE
        RAISE EXCEPTION 'operational action transition not allowed' USING ERRCODE = '55000';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$;
"""

OUTCOME_REVIEW = """
CREATE OR REPLACE FUNCTION core.check_outcome_review() RETURNS trigger LANGUAGE plpgsql
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
        IF EXISTS (SELECT 1 FROM core.operational_action a WHERE a.id = NEW.action_id AND a.completed_by = NEW.created_by) THEN
            RAISE EXCEPTION 'the outcome is reviewed by someone other than the person who completed the action'
                USING ERRCODE = '42501';
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
"""

# History rows are written here, after each change, as the table owner: the application role cannot
# insert them. The actor is the session user (or, for maintenance by the migrator, the recorded user).
HISTORY = """
CREATE FUNCTION core.record_workflow_transition() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, core AS $$
DECLARE
    me uuid := core.session_user_id();
    kind text;
    from_state text;
    to_state text;
    why text;
    actor uuid;
BEGIN
    IF TG_TABLE_NAME = 'pattern_review' THEN
        kind := 'PATTERN_REVIEW';
        IF TG_OP = 'INSERT' THEN
            from_state := NULL; to_state := 'DRAFT'; actor := NEW.created_by;
        ELSIF NEW.supervisor_status IS DISTINCT FROM OLD.supervisor_status THEN
            from_state := coalesce(OLD.supervisor_status, 'DRAFT');
            to_state := coalesce(NEW.supervisor_status, 'DRAFT');
            IF NEW.supervisor_status IN ('APPROVED', 'REJECTED', 'RETURNED') THEN
                why := NEW.decision_rationale; actor := NEW.decided_by;
            ELSE
                actor := NEW.created_by;
            END IF;
        ELSIF (NEW.analyst_narrative, NEW.recommended_next_step) IS DISTINCT FROM (OLD.analyst_narrative, OLD.recommended_next_step) THEN
            from_state := coalesce(OLD.supervisor_status, 'DRAFT'); to_state := 'REVISED'; actor := NEW.created_by;
        ELSE
            RETURN NULL;
        END IF;
    ELSIF TG_TABLE_NAME = 'operational_action' THEN
        kind := 'ACTION';
        IF TG_OP = 'INSERT' THEN
            from_state := NULL; to_state := 'PROPOSED'; actor := NEW.proposed_by;
        ELSIF NEW.approval_status IS DISTINCT FROM OLD.approval_status THEN
            from_state := OLD.approval_status; to_state := NEW.approval_status;
            why := NEW.approval_rationale; actor := NEW.approved_by;
        ELSIF NEW.completion_status IS DISTINCT FROM OLD.completion_status THEN
            from_state := 'APPROVED'; to_state := NEW.completion_status; actor := NEW.completed_by;
        ELSE
            RETURN NULL;
        END IF;
    ELSE
        kind := 'OUTCOME_REVIEW';
        IF TG_OP = 'INSERT' THEN
            from_state := NULL; to_state := 'DRAFT';
        ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
            from_state := OLD.status; to_state := NEW.status;
        ELSE
            from_state := 'DRAFT'; to_state := 'REVISED';
        END IF;
        actor := NEW.created_by;
    END IF;
    INSERT INTO core.workflow_transition (object_type, object_id, from_state, to_state, rationale, region_id,
                                          station_id, actor_id)
    VALUES (kind, NEW.id, from_state, to_state, why, NEW.region_id, NEW.station_id, coalesce(me, actor));
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION core.record_workflow_transition() FROM PUBLIC;
CREATE TRIGGER pattern_review_history AFTER INSERT OR UPDATE ON core.pattern_review
FOR EACH ROW EXECUTE FUNCTION core.record_workflow_transition();
CREATE TRIGGER operational_action_history AFTER INSERT OR UPDATE ON core.operational_action
FOR EACH ROW EXECUTE FUNCTION core.record_workflow_transition();
CREATE TRIGGER outcome_review_history AFTER INSERT OR UPDATE ON core.outcome_review
FOR EACH ROW EXECUTE FUNCTION core.record_workflow_transition();
REVOKE INSERT ON core.workflow_transition FROM crimemap_app;
"""


def upgrade() -> None:
    run_sql(
        PATTERN_REVIEW
        + OPERATIONAL_ACTION
        + OUTCOME_REVIEW
        + HISTORY
        + """
ALTER TABLE core.operational_action
    ADD CONSTRAINT operational_action_completer_differs CHECK (completed_by IS NULL OR completed_by <> approved_by);
ALTER TABLE core.outcome_review
    ADD CONSTRAINT outcome_review_comparison_keys CHECK (
        cardinality(comparison_keys) <= 200
        AND (comparison_kind <> 'NONE' OR cardinality(comparison_keys) = 0)
        AND array_to_string(comparison_keys, ',') ~ '^[A-Z0-9:,-]*$');
-- Item histories for data-quality issues and operational records are read from the audit log.
CREATE INDEX audit_event_object_idx ON audit.audit_event (object_type, object_id, seq);
"""
    )


def downgrade() -> None:
    # The stricter rule functions stay in place (their 0010 versions only allowed more); 0010's own
    # downgrade drops them.
    run_sql(
        """
DROP INDEX audit.audit_event_object_idx;
ALTER TABLE core.outcome_review DROP CONSTRAINT outcome_review_comparison_keys;
ALTER TABLE core.operational_action DROP CONSTRAINT operational_action_completer_differs;
GRANT INSERT (object_type, object_id, from_state, to_state, rationale, region_id, station_id, actor_id)
      ON core.workflow_transition TO crimemap_app;
DROP TRIGGER outcome_review_history ON core.outcome_review;
DROP TRIGGER operational_action_history ON core.operational_action;
DROP TRIGGER pattern_review_history ON core.pattern_review;
DROP FUNCTION core.record_workflow_transition();
"""
    )
