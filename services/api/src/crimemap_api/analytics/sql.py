"""SQL for the Analytics Workbench. Every statement reads ``core.analytics_selection`` (one
definition, ADR-032/035). Statements are literal text, generated from one template when this
file was written, so no SQL is composed at run time."""

from sqlalchemy import text

# Totals by category, record type, location usability and data quality (headline counts, category chart, DQ chart).
TOTALS = text(
    """
SELECT s.analytical_category_id AS category_id, s.record_type, s.masked,
       s.geometry IS NOT NULL AND s.beat_id IS NOT NULL AS located, s.quality, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
GROUP BY 1, 2, 3, 4, 5
"""
)

# Counts per time bucket (IST) and category.
SERIES = text(
    """
SELECT date_trunc(CAST(:grain AS text), s.event_at AT TIME ZONE 'Asia/Kolkata')::date AS bucket,
       s.analytical_category_id AS category_id, s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
GROUP BY 1, 2, 3
"""
)

# Counts per hexagon (pointy-top axial arithmetic, as in map.py) for usable locations.
CELLS = text(
    """
WITH s AS (
    SELECT s.x, s.y, s.masked FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
    WHERE s.geometry IS NOT NULL AND s.beat_id IS NOT NULL
),
f AS (SELECT (sqrt(3) / 3 * x - y / 3) / :size AS q, (2.0 / 3 * y) / :size AS r, * FROM s),
g AS (SELECT -q - r AS cy, round(q) AS rq, round(r) AS rr, round(-q - r) AS ry, * FROM f),
h AS (SELECT CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN -ry - rr ELSE rq END AS hq,
             CASE WHEN abs(rq - q) > abs(ry - cy) AND abs(rq - q) > abs(rr - r) THEN rr
                  WHEN abs(ry - cy) > abs(rr - r) THEN rr ELSE -rq - ry END AS hr, * FROM g)
SELECT h.hq::int AS cell_q, h.hr::int AS cell_r, h.masked, count(*) AS n FROM h GROUP BY 1, 2, 3
"""
)

# Counts per beat and station (choropleth).
UNITS = text(
    """
SELECT s.beat_id, s.station_id, s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
WHERE s.station_id IS NOT NULL
GROUP BY 1, 2, 3
"""
)

# Localities with records in several distinct weeks (repeat locations).
REPEAT = text(
    """
SELECT s.locality, s.station_id, s.masked,
       count(DISTINCT date_trunc('week', s.event_at AT TIME ZONE 'Asia/Kolkata')) AS weeks, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
WHERE s.locality IS NOT NULL
GROUP BY 1, 2, 3
"""
)

# Day of week x hour (IST).
DOW_HOUR = text(
    """
SELECT extract(isodow FROM s.event_at AT TIME ZONE 'Asia/Kolkata')::int AS dow,
       extract(hour FROM s.event_at AT TIME ZONE 'Asia/Kolkata')::int AS hour, s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
GROUP BY 1, 2, 3
"""
)

# Occurrence-to-report delay buckets.
DELAY = text(
    """
SELECT CASE WHEN s.occurred_at IS NULL OR s.reported_at IS NULL THEN 'unknown'
            WHEN s.reported_at < s.occurred_at THEN 'reported_before_occurrence'
            WHEN s.reported_at - s.occurred_at < interval '1 day' THEN 'same_day'
            WHEN s.reported_at - s.occurred_at < interval '3 days' THEN '1_2_days'
            WHEN s.reported_at - s.occurred_at < interval '8 days' THEN '3_7_days'
            WHEN s.reported_at - s.occurred_at < interval '31 days' THEN '8_30_days'
            ELSE 'over_30_days' END AS bucket, s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
GROUP BY 1, 2
"""
)

# Case status by month (CASE records only).
STATUS = text(
    """
SELECT date_trunc('month', s.event_at AT TIME ZONE 'Asia/Kolkata')::date AS bucket, s.status_code,
       s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
WHERE s.record_type = 'CASE'
GROUP BY 1, 2, 3
"""
)

# Location method, confidence and precision distribution.
DQ = text(
    """
SELECT coalesce(s.geocode_method, 'NONE') AS geocode_method,
       CASE WHEN s.confidence IS NULL THEN 'none' WHEN s.confidence < 0.4 THEN 'low'
            WHEN s.confidence < 0.8 THEN 'medium' ELSE 'high' END AS confidence_band,
       coalesce(s.precision_level, 'UNKNOWN') AS precision_level,
       s.geometry IS NULL AS no_location, s.geometry IS NOT NULL AND s.beat_id IS NULL AS outside,
       s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
GROUP BY 1, 2, 3, 4, 5, 6
"""
)

# Records in the period (by report time) without an occurrence time.
MISSING_OCCURRENCE = text(
    """
SELECT s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
WHERE s.occurred_at IS NULL
GROUP BY 1
"""
)

# Open data-quality issues on the selected records, by type.
OPEN_ISSUES = text(
    """
SELECT q.issue_type, q.severity, s.masked, count(*) AS n
FROM core.analytics_selection(CAST(:region AS uuid), CAST(:stations AS uuid[]), CAST(:beats AS uuid[]),
        CAST(:categories AS uuid[]), CAST(:record_types AS text[]), CAST(:statuses AS text[]),
        CAST(:p_from AS timestamptz), CAST(:p_to AS timestamptz), CAST(:basis AS text), CAST(:min_conf AS numeric),
        CAST(:sg AS boolean), CAST(:sr AS uuid[]), CAST(:ss AS uuid[]), CAST(:locality AS text),
        CAST(:hour_from AS integer), CAST(:hour_to AS integer), CAST(:sens_g AS boolean),
        CAST(:sens_r AS uuid[]), CAST(:sens_s AS uuid[])) s
JOIN core.data_quality_issue q ON q.incident_id = s.id
WHERE q.state IN ('OPEN', 'ASSIGNED')
GROUP BY 1, 2, 3
"""
)

HEX_POLYGONS = text(
    """
SELECT t.q, t.r, ST_AsGeoJSON(core.hex_polygon(t.q, t.r, :size, :crs), 6) AS geometry
FROM unnest(CAST(:qs AS int[]), CAST(:rs AS int[])) AS t(q, r)
"""
)

UNIT_SHAPES = text(
    """
SELECT b.org_unit_id AS unit_id, b.code, b.name, ST_AsGeoJSON(b.geometry, 5) AS geometry
FROM core.current_boundary b WHERE b.org_unit_id = ANY (CAST(:ids AS uuid[]))
"""
)

CATEGORIES = text("SELECT id, code, name FROM core.analytical_category")

FEEDS = text(
    """
SELECT code, name, approved_status, last_batch_at, last_health_status, stale_after_hours, open_dead_letters
FROM core.source_health ORDER BY code
"""
)

PATTERN_REVIEWS_OPEN = text(
    """
SELECT count(*) FROM core.pattern_review
WHERE region_id = :region AND analyst_status IN ('DRAFT', 'SUBMITTED')
  AND (CAST(:stations AS uuid[]) IS NULL OR station_id = ANY (CAST(:stations AS uuid[])))
  AND period_start <= :p_end AND period_end >= :p_start
"""
)
