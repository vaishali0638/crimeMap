"""Export retention (Phase 5): exports expire 24 hours after creation (core.report_export CHECK) and
are deleted by the hourly ``purge_expired_exports`` job."""

from sqlalchemy import text

PURGE_EXPORTS = text(
    "WITH gone AS (DELETE FROM core.report_export WHERE expires_at <= now() RETURNING 1) "
    "SELECT count(*) FROM gone"
)
