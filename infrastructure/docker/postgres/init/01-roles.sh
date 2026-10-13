#!/usr/bin/env bash
# Runs once, as the PostgreSQL superuser, when the data directory is first initialised.
# Creates the CRIMEMAP database and three login roles with least privilege (ADR-002):
#   crimemap_migrator  owns the database and schemas; runs Alembic migrations only
#   crimemap_app       used by the API; no ownership, no BYPASSRLS, no DDL
#   crimemap_worker    used by background jobs; narrow grants
# Passwords come from the environment (never committed). Table-level grants are made by migrations.
set -euo pipefail

: "${CRIMEMAP_DB_NAME:?CRIMEMAP_DB_NAME is required}"
: "${CRIMEMAP_MIGRATOR_PASSWORD:?CRIMEMAP_MIGRATOR_PASSWORD is required}"
: "${CRIMEMAP_APP_PASSWORD:?CRIMEMAP_APP_PASSWORD is required}"
: "${CRIMEMAP_WORKER_PASSWORD:?CRIMEMAP_WORKER_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$CRIMEMAP_DB_NAME" \
  -v migrator_pw="$CRIMEMAP_MIGRATOR_PASSWORD" \
  -v app_pw="$CRIMEMAP_APP_PASSWORD" \
  -v worker_pw="$CRIMEMAP_WORKER_PASSWORD" <<'SQL'
CREATE ROLE crimemap_migrator LOGIN PASSWORD :'migrator_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE crimemap_app      LOGIN PASSWORD :'app_pw'      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE ROLE crimemap_worker   LOGIN PASSWORD :'worker_pw'   NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE DATABASE :"db" OWNER crimemap_migrator;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$CRIMEMAP_DB_NAME" <<'SQL'
-- Extensions need superuser; create them here so migrations never need elevated rights.
CREATE EXTENSION IF NOT EXISTS postgis;
-- Nobody but the owner may create objects in public.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO crimemap_app, crimemap_worker;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$CRIMEMAP_DB_NAME" \
  -v db="$CRIMEMAP_DB_NAME" <<'SQL'
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO crimemap_migrator, crimemap_app, crimemap_worker;
SQL
