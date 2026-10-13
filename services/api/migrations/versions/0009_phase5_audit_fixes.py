"""Phase 5 audit fixes: evidence snapshots record everything their result covers.

- ``region_ids`` / ``station_ids``: every unit the stored result covers (a comparison covers both
  sides; a station-scoped caller's region-wide view covers their stations). ``station_ids`` NULL
  means whole regions. Readers other than the author must have the section's permission over all
  of them (checked in the API) — before, a snapshot was authorised on its first region only.
- ``may_mask``: whether sensitive categories were combined for the creator. A snapshot computed
  with sensitive access is shown only to readers whose sensitive access covers it.
- Row-level security also admits station-scoped users by station overlap, so a station officer's
  region-wide snapshot no longer fails the insert check (500).
- ``report_export.section`` is constrained like ``evidence_snapshot.section``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
ALTER TABLE core.evidence_snapshot
    ADD COLUMN region_ids uuid[] NOT NULL DEFAULT '{}',
    ADD COLUMN station_ids uuid[],
    ADD COLUMN may_mask boolean NOT NULL DEFAULT true;
UPDATE core.evidence_snapshot
   SET region_ids = ARRAY[region_id],
       station_ids = CASE WHEN station_id IS NULL THEN NULL ELSE ARRAY[station_id] END,
       may_mask = (meta->>'suppressed') IS NOT NULL;
ALTER TABLE core.evidence_snapshot ADD CONSTRAINT evidence_snapshot_covers_region
    CHECK (cardinality(region_ids) >= 1 AND region_id = ANY (region_ids));

DROP POLICY evidence_app_scope ON core.evidence_snapshot;
CREATE POLICY evidence_app_scope ON core.evidence_snapshot FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids'))
       OR station_ids && core.scope_ids('app.station_ids')
       OR created_by = core.session_user_id())
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids'))
            OR station_ids && core.scope_ids('app.station_ids')
            OR (SELECT core.scope_global()));
GRANT INSERT (region_ids, station_ids, may_mask) ON core.evidence_snapshot TO crimemap_app;

ALTER TABLE core.report_export ADD CONSTRAINT report_export_section_check
    CHECK (section IN ('overview','location','case_trends','comparison','data_quality'));
"""
    )


def downgrade() -> None:
    run_sql(
        """
ALTER TABLE core.report_export DROP CONSTRAINT report_export_section_check;
REVOKE INSERT (region_ids, station_ids, may_mask) ON core.evidence_snapshot FROM crimemap_app;
DROP POLICY evidence_app_scope ON core.evidence_snapshot;
CREATE POLICY evidence_app_scope ON core.evidence_snapshot FOR ALL TO crimemap_app
USING (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids'))
       OR created_by = core.session_user_id())
WITH CHECK (region_id = ANY (core.scope_ids('app.region_ids')) OR station_id = ANY (core.scope_ids('app.station_ids'))
            OR (SELECT core.scope_global()));
ALTER TABLE core.evidence_snapshot DROP CONSTRAINT evidence_snapshot_covers_region;
ALTER TABLE core.evidence_snapshot DROP COLUMN may_mask, DROP COLUMN station_ids, DROP COLUMN region_ids;
"""
    )
