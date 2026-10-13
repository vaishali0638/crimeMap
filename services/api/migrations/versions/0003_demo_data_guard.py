"""PRODUCTION start-up guard: ``core.demo_data_present()`` (docs/demo-data.md, policy 5).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""

from crimemap_api.migration_sql import run_sql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql(
        """
        -- PRODUCTION start-up guard (demo-data policy 5): true if any synthetic row or demo identity exists.
        -- SECURITY DEFINER so the check sees past row-level security; it returns a single boolean.
        CREATE FUNCTION core.demo_data_present() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, core AS $$
            SELECT EXISTS (SELECT 1 FROM core.incident_record WHERE is_demo)
                OR EXISTS (SELECT 1 FROM core.organizational_unit WHERE is_demo)
                OR EXISTS (SELECT 1 FROM core.app_user WHERE is_demo)
                OR EXISTS (SELECT 1 FROM core.source_system WHERE approved_status = 'DEMO' AND last_batch_at IS NOT NULL)
        $$;
        REVOKE ALL ON FUNCTION core.demo_data_present() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION core.demo_data_present() TO crimemap_app;
        """
    )


def downgrade() -> None:
    run_sql("DROP FUNCTION core.demo_data_present();")
