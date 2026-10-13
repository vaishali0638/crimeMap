"""Phase 4 (PY Dashboard): the shared map selection also returns each record's time on the chosen
basis and its locality, so the trend boards (time of day, month, week, location) read the very same
selection as the map (ADR-032: one definition, counts that agree).

The function's argument list and existing columns are unchanged; two columns are appended
(PostgreSQL cannot change a function's result type in place, so it is dropped and recreated in
this transaction).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_ARGS = """p_region uuid, p_stations uuid[], p_beats uuid[], p_categories uuid[], p_record_types text[],
    p_statuses text[], p_from timestamptz, p_to timestamptz, p_basis text, p_min_confidence numeric,
    p_scope_global boolean, p_scope_regions uuid[], p_scope_stations uuid[]"""

_SIGNATURE = """uuid, uuid[], uuid[], uuid[], text[], text[], timestamptz, timestamptz, text, numeric,
    boolean, uuid[], uuid[]"""

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


def upgrade() -> None:
    run_sql(
        f"""
DROP FUNCTION core.map_selection({_SIGNATURE});
CREATE FUNCTION core.map_selection(
    {_ARGS})
RETURNS TABLE (id uuid, region_id uuid, station_id uuid, beat_id uuid, analytical_category_id uuid,
               is_sensitive boolean, record_type text, status_code text, quality text,
               geometry geometry, x double precision, y double precision, crs integer,
               is_residential boolean, precision_level text,
               event_at timestamptz, locality text)
LANGUAGE sql STABLE AS $$
    SELECT i.id, i.region_id, i.station_id, i.beat_id, i.analytical_category_id,
           i.is_sensitive_category, i.record_type, i.status_code, i.record_quality_status,
           l.geometry, l.analysis_x, l.analysis_y, l.analysis_crs,
           coalesce(l.is_residential, false), l.precision_level,
           CASE WHEN p_basis = 'REPORTED' THEN i.reported_at ELSE i.occurrence_start_at END,
           l.locality
{_WHERE}
GRANT EXECUTE ON FUNCTION core.map_selection({_SIGNATURE}) TO crimemap_app;
"""
    )


def downgrade() -> None:
    run_sql(
        f"""
DROP FUNCTION core.map_selection({_SIGNATURE});
CREATE FUNCTION core.map_selection(
    {_ARGS})
RETURNS TABLE (id uuid, region_id uuid, station_id uuid, beat_id uuid, analytical_category_id uuid,
               is_sensitive boolean, record_type text, status_code text, quality text,
               geometry geometry, x double precision, y double precision, crs integer,
               is_residential boolean, precision_level text)
LANGUAGE sql STABLE AS $$
    SELECT i.id, i.region_id, i.station_id, i.beat_id, i.analytical_category_id,
           i.is_sensitive_category, i.record_type, i.status_code, i.record_quality_status,
           l.geometry, l.analysis_x, l.analysis_y, l.analysis_crs,
           coalesce(l.is_residential, false), l.precision_level
{_WHERE}
GRANT EXECUTE ON FUNCTION core.map_selection({_SIGNATURE}) TO crimemap_app;
"""
    )
