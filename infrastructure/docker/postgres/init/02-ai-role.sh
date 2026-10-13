#!/usr/bin/env bash
# Runs as the PostgreSQL superuser (once at initialisation; safe to run again on an existing
# database: `scripts/compose.sh exec -T postgres bash /docker-entrypoint-initdb.d/02-ai-role.sh`).
#
# crimemap_ai (Phase 7, ADR-037, revised by the Phase 7 audit): the login the AI detectors use, in
# their own container (ai-worker) that holds no other database credentials. Migrations 0012/0013
# grant it SELECT on approved inputs, INSERT on AI runs and insights, and INSERT on the audit log
# only. It is not a member of any other role and no other role is a member of it, so detector code
# cannot switch to a role that may create an action, export data or change a case.
set -euo pipefail

: "${CRIMEMAP_DB_NAME:?CRIMEMAP_DB_NAME is required}"
: "${CRIMEMAP_AI_PASSWORD:?CRIMEMAP_AI_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname "$CRIMEMAP_DB_NAME" \
  -v ai_pw="$CRIMEMAP_AI_PASSWORD" -v db="$CRIMEMAP_DB_NAME" <<'SQL'
SELECT NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crimemap_ai') AS missing \gset
\if :missing
CREATE ROLE crimemap_ai LOGIN PASSWORD :'ai_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
\else
ALTER ROLE crimemap_ai LOGIN PASSWORD :'ai_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
\endif
-- Phase 7 (before its audit) let the worker switch to this role; that path is closed.
REVOKE crimemap_ai FROM crimemap_worker;
GRANT CONNECT ON DATABASE :"db" TO crimemap_ai;
GRANT USAGE ON SCHEMA public TO crimemap_ai;
SQL
