"""Identity tables, region reference data, and the append-only hash-chained audit log.

Revision ID: 0001
Revises:
Create Date: 2026-10-11

Runs as crimemap_migrator (schema owner). The API role gets only the privileges listed in GRANTS.
"""

from crimemap_api.migration_sql import run_sql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMAS = """
CREATE SCHEMA core;
CREATE SCHEMA audit;
REVOKE ALL ON SCHEMA core, audit FROM PUBLIC;
GRANT USAGE ON SCHEMA core, audit TO crimemap_app, crimemap_worker;
"""

CORE = """
CREATE TABLE core.organizational_unit (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_type     text NOT NULL CHECK (unit_type IN ('UT','REGION','DISTRICT','SUBDIVISION','STATION','BEAT')),
    parent_id     uuid REFERENCES core.organizational_unit (id),
    name          text NOT NULL,
    code          text NOT NULL UNIQUE,
    analysis_crs  integer CHECK (analysis_crs IN (32643, 32644)),
    is_demo       boolean NOT NULL DEFAULT false,
    active_from   date NOT NULL DEFAULT current_date,
    active_to     date,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (unit_type <> 'REGION' OR analysis_crs IS NOT NULL)
);

-- The four geographically separated regions. Analysis CRS per ADR-005: Mahe is in UTM 43N.
INSERT INTO core.organizational_unit (unit_type, name, code) VALUES ('UT', 'Puducherry (UT)', 'PY');
INSERT INTO core.organizational_unit (unit_type, parent_id, name, code, analysis_crs)
SELECT 'REGION', ut.id, r.name, r.code, r.crs
FROM core.organizational_unit ut,
     (VALUES ('Puducherry', 'PDY', 32644), ('Karaikal', 'KKL', 32644),
             ('Mahe', 'MAH', 32643), ('Yanam', 'YAN', 32644)) AS r(name, code, crs)
WHERE ut.code = 'PY';

CREATE TABLE core.app_user (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    idp_subject         text NOT NULL UNIQUE,
    username            text,
    name                text,
    employee_reference  text UNIQUE,
    email               text,
    status              text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUSPENDED','DISABLED')),
    mfa_status          text NOT NULL DEFAULT 'NOT_ENROLLED' CHECK (mfa_status IN ('NOT_ENROLLED','ENROLLED')),
    last_login_at       timestamptz,
    is_demo             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.role (
    code         text PRIMARY KEY,
    name         text NOT NULL,
    description  text
);

CREATE TABLE core.permission (
    code         text PRIMARY KEY,
    description  text
);

CREATE TABLE core.role_permission (
    role_code        text NOT NULL REFERENCES core.role (code) ON DELETE CASCADE,
    permission_code  text NOT NULL REFERENCES core.permission (code) ON DELETE CASCADE,
    scope            text NOT NULL,
    aggregate_only   boolean NOT NULL,
    reauth           boolean NOT NULL,
    purpose          text,
    note             text,
    PRIMARY KEY (role_code, permission_code)
);

CREATE TABLE core.user_role_grant (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 uuid NOT NULL REFERENCES core.app_user (id),
    role_code               text NOT NULL REFERENCES core.role (code),
    scope_region_ids        uuid[] NOT NULL DEFAULT '{}',
    scope_station_ids       uuid[] NOT NULL DEFAULT '{}',
    scope_beat_ids          uuid[] NOT NULL DEFAULT '{}',
    classification_ceiling  text NOT NULL DEFAULT 'RESTRICTED'
                            CHECK (classification_ceiling IN ('INTERNAL','RESTRICTED','HIGHLY_RESTRICTED')),
    record_types            text[] NOT NULL DEFAULT '{}',
    export_scope            text[] NOT NULL DEFAULT '{}',
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_to                timestamptz,
    grant_reason            text NOT NULL CHECK (length(grant_reason) BETWEEN 3 AND 500),
    granted_by              uuid REFERENCES core.app_user (id),
    revoked_at              timestamptz,
    revoked_by              uuid REFERENCES core.app_user (id),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (granted_by IS NULL OR granted_by <> user_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX user_role_grant_user_idx ON core.user_role_grant (user_id) WHERE revoked_at IS NULL;
"""

AUDIT = r"""
CREATE TABLE audit.chain_head (
    id         smallint PRIMARY KEY CHECK (id = 1),
    seq        bigint NOT NULL,
    last_hash  text NOT NULL
);
INSERT INTO audit.chain_head VALUES (1, 0, repeat('0', 64));

CREATE TABLE audit.audit_event (
    id                     uuid NOT NULL DEFAULT gen_random_uuid(),
    seq                    bigint NOT NULL,
    occurred_at            timestamptz NOT NULL DEFAULT now(),
    actor_user_id          uuid,
    actor_subject          text,
    actor_roles            text[] NOT NULL DEFAULT '{}',
    action                 text NOT NULL CHECK (action ~ '^[A-Z][A-Z_]{1,63}$'),
    object_type            text,
    object_id              text,
    request_id             text,
    ip_address             inet,
    user_agent_hash        text,
    purpose                text,
    filters                jsonb,
    result                 text NOT NULL CHECK (result IN ('ALLOW','DENY','ERROR')),
    reason                 text,
    before_meta            jsonb,
    after_meta             jsonb,
    export_classification  text,
    environment            text NOT NULL CHECK (environment IN ('DEMO','SANDBOX','PRODUCTION')),
    prev_hash              text NOT NULL,
    row_hash               text NOT NULL,
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE audit.audit_event_default PARTITION OF audit.audit_event DEFAULT;
CREATE INDEX audit_event_seq_idx ON audit.audit_event (seq);
CREATE INDEX audit_event_actor_idx ON audit.audit_event (actor_user_id, occurred_at);
CREATE INDEX audit_event_action_idx ON audit.audit_event (action, occurred_at);
CREATE INDEX audit_event_request_idx ON audit.audit_event (request_id);

-- Monthly partitions (ADR-008). The worker creates future months; these cover the first ones.
CREATE FUNCTION audit.ensure_month_partition(p_month date) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit AS $$
DECLARE
    v_start date := date_trunc('month', p_month)::date;
    v_name  text := format('audit_event_%s', to_char(v_start, 'YYYY_MM'));
BEGIN
    IF to_regclass(format('audit.%I', v_name)) IS NULL THEN
        EXECUTE format(
            'CREATE TABLE audit.%I PARTITION OF audit.audit_event FOR VALUES FROM (%L) TO (%L)',
            v_name, v_start, (v_start + interval '1 month')::date);
    END IF;
END $$;
SELECT audit.ensure_month_partition((date_trunc('month', now()) + make_interval(months => m))::date)
FROM generate_series(0, 2) AS m;

-- Hash input: the row as JSON, minus the hash columns, nulls stripped (so columns added later do
-- not change the hash of older rows), rendered with TimeZone fixed to UTC.
CREATE FUNCTION audit.chain_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, audit SET TimeZone = 'UTC' AS $$
DECLARE
    v_seq  bigint;
    v_prev text;
BEGIN
    SELECT seq, last_hash INTO v_seq, v_prev FROM audit.chain_head WHERE id = 1 FOR UPDATE;
    NEW.seq := v_seq + 1;
    NEW.prev_hash := v_prev;
    NEW.row_hash := encode(sha256(convert_to(
        v_prev || jsonb_strip_nulls(to_jsonb(NEW) - 'prev_hash' - 'row_hash')::text, 'UTF8')), 'hex');
    UPDATE audit.chain_head SET seq = NEW.seq, last_hash = NEW.row_hash WHERE id = 1;
    RETURN NEW;
END $$;

CREATE TRIGGER audit_event_chain BEFORE INSERT ON audit.audit_event
FOR EACH ROW EXECUTE FUNCTION audit.chain_insert();

CREATE FUNCTION audit.reject_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit events are immutable';
END $$;

CREATE TRIGGER audit_event_no_update BEFORE UPDATE OR DELETE ON audit.audit_event
FOR EACH ROW EXECUTE FUNCTION audit.reject_change();
CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit.audit_event
FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_change();

CREATE FUNCTION audit.verify_chain()
RETURNS TABLE (ok boolean, checked bigint, first_bad_seq bigint, problem text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, audit SET TimeZone = 'UTC' AS $$
DECLARE
    r          record;
    v_prev     text := repeat('0', 64);
    v_expected bigint := 1;
    v_count    bigint := 0;
    v_head     bigint;
BEGIN
    FOR r IN SELECT * FROM audit.audit_event ORDER BY seq LOOP
        v_count := v_count + 1;
        IF r.seq <> v_expected THEN
            RETURN QUERY SELECT false, v_count, v_expected, 'gap'::text; RETURN;
        END IF;
        IF r.prev_hash <> v_prev THEN
            RETURN QUERY SELECT false, v_count, r.seq, 'broken_link'::text; RETURN;
        END IF;
        IF r.row_hash <> encode(sha256(convert_to(
               v_prev || jsonb_strip_nulls(to_jsonb(r) - 'prev_hash' - 'row_hash')::text, 'UTF8')), 'hex') THEN
            RETURN QUERY SELECT false, v_count, r.seq, 'hash_mismatch'::text; RETURN;
        END IF;
        v_prev := r.row_hash;
        v_expected := v_expected + 1;
    END LOOP;
    SELECT seq INTO v_head FROM audit.chain_head WHERE id = 1;
    IF v_head <> v_expected - 1 THEN
        RETURN QUERY SELECT false, v_count, v_expected, 'tail_missing'::text; RETURN;
    END IF;
    RETURN QUERY SELECT true, v_count, NULL::bigint, NULL::text;
END $$;

-- Keycloak event import bookkeeping (worker): de-duplication and cursor.
CREATE TABLE audit.idp_event_import (
    event_key    text PRIMARY KEY,
    imported_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit.idp_sync_cursor (
    source         text PRIMARY KEY,
    last_event_ms  bigint NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);
"""

GRANTS = """
REVOKE ALL ON ALL TABLES IN SCHEMA core, audit FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA audit FROM PUBLIC;

-- API role
GRANT SELECT ON core.organizational_unit, core.role, core.permission, core.role_permission,
                core.app_user, core.user_role_grant TO crimemap_app;
GRANT INSERT (idp_subject, username, name, email, mfa_status, last_login_at, is_demo)
      ON core.app_user TO crimemap_app;
GRANT UPDATE (username, name, email, mfa_status, last_login_at, updated_at) ON core.app_user TO crimemap_app;
GRANT INSERT (user_id, role_code, scope_region_ids, scope_station_ids, scope_beat_ids,
              classification_ceiling, record_types, export_scope, valid_from, valid_to,
              grant_reason, granted_by) ON core.user_role_grant TO crimemap_app;
GRANT UPDATE (revoked_at, revoked_by, updated_at) ON core.user_role_grant TO crimemap_app;

-- Audit: append-only. Chain columns (seq, occurred_at, prev_hash, row_hash) are set by the trigger.
GRANT INSERT (id, actor_user_id, actor_subject, actor_roles, action, object_type, object_id,
              request_id, ip_address, user_agent_hash, purpose, filters, result, reason,
              before_meta, after_meta, export_classification, environment)
      ON audit.audit_event TO crimemap_app, crimemap_worker;
GRANT SELECT ON audit.audit_event TO crimemap_app;
GRANT EXECUTE ON FUNCTION audit.verify_chain() TO crimemap_app, crimemap_worker;

-- Worker role
GRANT SELECT (id, idp_subject) ON core.app_user TO crimemap_worker;
GRANT SELECT, INSERT ON audit.idp_event_import TO crimemap_worker;
GRANT SELECT, INSERT, UPDATE ON audit.idp_sync_cursor TO crimemap_worker;
GRANT EXECUTE ON FUNCTION audit.ensure_month_partition(date) TO crimemap_worker;
"""


def upgrade() -> None:
    for block in (SCHEMAS, CORE, AUDIT, GRANTS):
        run_sql(block)


def downgrade() -> None:
    run_sql("DROP SCHEMA audit CASCADE; DROP SCHEMA core CASCADE;")
