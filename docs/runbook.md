# CRIMEMAP Operations Runbook

**Environment:** DEMO, synthetic data only. Not a production-readiness claim. Procedures marked
**(verified)** were run in Phase 8 on the local Compose stack; the others are written from the code
and have not been exercised. Security incidents (mass export, unusual access, privilege changes)
follow [incident-response.md](incident-response.md). Deployment and promotion:
[deployment.md](deployment.md). Decisions: ADR-038 (observability), ADR-039 (backups).

## 1. Services at a glance

| Service | Compose name | Purpose | Health |
|---|---|---|---|
| Web BFF | host process (`pnpm start`, :3100) | Next.js UI and the browser-to-API proxy; holds sessions in Redis | page loads; `/signed-out` answers 200 |
| API | `api` (:8000) | FastAPI; every authorisation decision; audit writes | `GET /api/v1/health` (liveness), `GET /api/v1/ready` (database and cache) |
| Worker | `worker` | Ingestion, IdP event import, audit chain verification, exports | logs (`inbox_ingested`, `audit_chain_verified`) |
| Scheduler | `scheduler` | Enqueues the periodic jobs (§2.4); run exactly one | logs |
| AI worker | `ai-worker` | Detectors, as the `crimemap_ai` database login only | `AI_RUN` audit events |
| Database | `postgres` | PostgreSQL 16 + PostGIS; row-level security | `pg_isready` healthcheck |
| Cache / broker | `redis` | Rate limits, sessions, job queue | healthcheck |
| Identity | `keycloak` (:8080) | Sign-in with password and authenticator (TOTP) | healthcheck |
| Object store | `objectstore` (profile `storage`) | S3-compatible store (development) | healthcheck |
| Prometheus, Grafana | profile `observability` (:9090, :3300) | Metrics, alerts, dashboard | not run in Phase 8 (images not pulled) |

Start, stop and rebuild with `scripts/compose.sh` (it reads `.env`):

```bash
scripts/compose.sh --profile app up -d
```

## 2. Monitoring

### 2.1 Health and readiness

`/api/v1/health` answers 200 while the process runs. `/api/v1/ready` answers 503 with
`{"database": false}` or `{"cache": false}` when a dependency is down (2-second checks). Both are
public, rate-limited and say nothing else.

### 2.2 Metrics and dashboards

The API serves Prometheus metrics at `GET /metrics` (not under `/api/v1`, not proxied by the BFF),
with the bearer token `CRIMEMAP_METRICS_TOKEN` (404 when no token is configured, 401 for a wrong one)
**(verified)**:

| Metric | Meaning |
|---|---|
| `crimemap_http_requests_total{method,route,status}` | Requests by route **template** (never a raw path or identifier) |
| `crimemap_http_request_duration_seconds` | Latency histogram per route template (buckets 50 ms to 10 s) |
| `crimemap_signals_up` | 1 if the audit-derived signals below could be read |
| `crimemap_security_*`, `crimemap_ingest_*`, `crimemap_ai_run_failures_24h`, `crimemap_request_errors_15m` | Counts from the audit log over fixed windows (`services/api/src/crimemap_api/metrics.py`); counts only, no user, address, region or record |

Signals are read with the application role and cached for 30 seconds, so scraping cannot load the
database. With the `observability` profile, Prometheus scrapes every 30 seconds
(`infrastructure/observability/prometheus.yml`) and Grafana provisions the dashboard
"CRIMEMAP — operations and security" (`infrastructure/observability/grafana/`). Grafana's admin
password is in `secrets/grafana-admin-password` (created by `scripts/dev_bootstrap.sh`); anonymous
access and telemetry are off. Compose mounts these two secret files unchanged, so they are 0644 (the
containers run as unprivileged users) inside the 0700 `secrets/` directory; re-run the bootstrap if
an older copy is 0600.

### 2.3 Alerts

Rules: `infrastructure/observability/alerts.yml`. A unit test checks that every rule uses only metrics
the API exports and links to a section that exists here or in incident-response.md. No Alertmanager
is configured in DEMO; production routes alerts to the approved internal on-call channel (§8).

Thresholds are demo values. Before production, set them from four weeks of real traffic: record the
95th percentile of each signal, set warning thresholds above it, and review false alerts monthly.

### 2.4 Logs and scheduled jobs

All Python services log one JSON object per line with the request ID; headers, bodies and tokens are
never logged, and secret-like keys are redacted. The BFF logs security events (for example
`csrf_rejected`) the same way. Find a request across services by its `X-Request-ID`, which is also
stored on its audit events.

| Job | Interval | Failure shows as |
|---|---|---|
| `sync_idp_events` | `CRIMEMAP_WORKER_IDP_POLL_SECONDS` (60 s) | worker log errors |
| `verify_audit_chain` | daily | log `audit_chain_broken` (§3.7) |
| `ensure_audit_partitions` | daily | audit inserts failing when a month has no partition |
| `ingest_inbox_batches` | `CRIMEMAP_WORKER_INBOX_POLL_SECONDS` (300 s) | alerts `CrimemapIngestionFailed`, `CrimemapSourceStale` |
| `check_sources` | `CRIMEMAP_WORKER_SOURCE_HEALTH_SECONDS` (300 s) | stale-source data-quality issues |
| `reconcile_yesterday` | daily | data-quality issue `DQ-REC-001` |
| `purge_expired_exports` | hourly | exports kept past expiry |
| AI detectors (`ai-worker`) | daily loop | alert `CrimemapAiModelFailure` |

## 3. Responding to operational alerts

Each section starts with the alert names that link here.

### 3.1 API down or not ready

`CrimemapApiDown`.
1. `scripts/compose.sh ps api` and `scripts/compose.sh logs --tail=200 api`.
2. If `/api/v1/ready` reports `database: false`, go to §3.2; `cache: false`, check `redis`.
3. A start-up refusal is deliberate when configuration is unsafe (for example demo data in
   PRODUCTION, an http identity provider without `CRIMEMAP_ALLOW_INSECURE_OIDC` outside DEMO, or a
   route without an authorisation requirement). Fix the configuration; do not bypass the check.
4. Restart: `scripts/compose.sh --profile app up -d --force-recreate --no-deps api`.

### 3.2 Database unavailable

`CrimemapSignalsUnavailable` (security alerts are blind while this fires).
1. `scripts/compose.sh ps postgres`; check memory: the container is limited to 768 MB (ADR-018).
2. Logs: `scripts/compose.sh logs --tail=200 postgres`. Out-of-memory kills point to load (§3.4).
3. If the data volume is lost or corrupt, follow the disaster-recovery procedure (§5).

### 3.3 Error rate

`CrimemapHighErrorRate`, `CrimemapRequestErrorsAudited`.
1. Every unhandled error is audited as `REQUEST_ERROR` with the request ID; the API log has the
   exception type (never its message, which can contain data).
2. Group by route in Grafana ("Requests per second by status"); compare with recent deployments.
3. Roll back the last deployment if errors started with it ([deployment.md](deployment.md) §5).

### 3.4 Slow map or analytics

`CrimemapMapLatencyHigh`, `CrimemapAnalyticsLatencyHigh`.
1. Check database CPU (`docker stats crimemap-postgres-1`). Phase 8 measured the database as the
   bottleneck: both vCPUs saturated by ten concurrent long-period hexagon queries
   ([phase-8-report.md](phase-8-report.md) §3).
2. Look for long queries: as the migrator, `SELECT pid, now() - query_start, left(query, 80) FROM
   pg_stat_activity WHERE state = 'active' ORDER BY 2 DESC;`. The API's 15-second statement timeout
   ends runaway queries.
3. Short term: more CPU for the database. Longer term: the options in ADR-017.

### 3.5 Ingestion failure

`CrimemapIngestionFailed`, `CrimemapSourceStale`.
1. Data Health (`/data-health`) shows each source's last batch, counts and dead letters.
2. Rejected files are moved to `rejected/`, failed ones to `failed/` in the inbox, with an
   `INGEST_REJECTED` or `INGEST_FAILED` audit event (reason: bad signature, malformed file, source
   mismatch, unapproved source).
3. A rejected signature is a security signal: confirm with the source owner that they sent the file
   before re-sending it. Never re-sign a received file.
4. After fixing the cause, put the file back in the inbox; ingestion is idempotent (re-sent records
   count as duplicates, not new incidents).

### 3.6 AI detector failure

`CrimemapAiModelFailure`.
1. The failed model and region are a FAILED run with an `AI_RUN` ERROR audit event; other detectors
   continued.
2. Logs: `scripts/compose.sh logs --tail=200 ai-worker` (`ai_detector_failed`, with model and
   region codes).
3. If a model keeps failing, switch it off in "AI models" (`/ai`, system administrator, fresh sign-in,
   reason) — see [ai-governance.md](ai-governance.md) §6.

### 3.7 Audit chain broken

The daily `verify_audit_chain` job logs `audit_chain_broken` with the first bad sequence number.
1. Treat as a security incident ([incident-response.md](incident-response.md), evidence preservation
   first): do not restart or "repair" the database.
2. An auditor can confirm with `GET /api/v1/audit/verification`.
3. Compare with the latest verified backup's manifest (§4: `audit_chain.checked`).

## 4. Backups and restore (ADR-039)

### 4.1 Taking a backup (verified)

```bash
set -a; source .env; set +a
services/api/.venv/bin/python scripts/backup_restore.py backup
```

- One consistent snapshot. The manifest (row counts of all 36 tables, migration version, audit-chain
  check, row-level security and policy counts, SHA-256 of the file) is read in the snapshot that
  `pg_dump` uses.
- Encrypted inside the database container with GnuPG (AES-256, integrity-protected), with
  `CRIMEMAP_BACKUP_PASSPHRASE`, so no plaintext dump reaches the host disk.
- Written to `backups/` (git-ignored), files mode 600.

Phase 8 live run: 1.2 GB database to an 85 MiB encrypted file in about 7 seconds.

### 4.2 Restore test (verified)

```bash
services/api/.venv/bin/python scripts/backup_restore.py restore-test backups/<file>.dump.gpg
```

The test:
- checks the file's SHA-256;
- decrypts and restores it into the scratch database `crimemap_restore_test` on the same server
  (never the live one);
- compares every table count, the migration version, the audit hash chain, row-level security and
  policy counts with the manifest;
- checks that the application role sees no case data without a scope and all of it with one.

The scratch database is then dropped, and a JSON report is written beside the backup. Phase 8 live
run: passed, 871,589 rows in 36 tables, in about 10 seconds. The integration tests repeat it on every
run, with a wrong passphrase and two tampered files, which are refused.

Run a restore test after every backup that will be kept, and at least monthly.

### 4.3 Restoring for real (not exercised)

1. Stop writers: `scripts/compose.sh --profile app stop api worker scheduler ai-worker` (and the web
   BFF).
2. Restore into the scratch database and keep it:
   `scripts/backup_restore.py restore-test <file> --keep` (it must pass).
3. As the PostgreSQL superuser, swap the databases:
   `ALTER DATABASE crimemap RENAME TO crimemap_replaced;` then
   `ALTER DATABASE crimemap_restore_test RENAME TO crimemap;`.
4. Start the services and check `/api/v1/ready`, the audit verification and Data Health.
5. Keep `crimemap_replaced` until the incident review is closed (evidence), then drop it.

Data written after the backup is lost (no point-in-time recovery in DEMO).

### 4.4 Production

Use the platform's managed, encrypted backups with WAL archiving (point-in-time recovery), stored in
a separate account or region, with restore tests on a schedule. Keep the backup encryption key in
the key-management service and an offline escrow copy. Use a dedicated backup role (read-only, row
security bypass) instead of the superuser.

## 5. Disaster recovery

Proposed targets (owner to confirm): recovery point objective 24 hours in DEMO (daily backup);
recovery time objective 2 hours. Production targets depend on the hosting design (threat model §9).

| Scenario | Response |
|---|---|
| Database volume lost or corrupt | New volume: `scripts/compose.sh up -d postgres` (init scripts recreate the roles), then §4.3 from the latest tested backup. Migrations are already in the backup; do not re-run demo data generation. |
| Wrong data written (bad ingest, operator error) | Prefer correction at source and re-ingestion (idempotent). Restore only if correction is impossible; record the decision. |
| Host lost | Rebuild the host, restore `.env` and `secrets/` from the secrets escrow, rebuild images from the repository, then §4.3. |
| Credentials exposed | §6 for every exposed secret, then [incident-response.md](incident-response.md). |
| Identity provider lost | Users cannot sign in; data is safe. Restore Keycloak from its own backup (realm export); authenticator enrolments are lost if the realm is re-imported, and users re-enrol. |

After any recovery: run the restore test on the next backup, verify the audit chain, and write an
incident record.

## 6. Key and secret rotation

Local secrets live in `.env` and `secrets/` (git-ignored, mode 600), generated by
`scripts/dev_bootstrap.sh`; production secrets come from a secrets manager. Rotate on exposure, on
staff change and at least yearly.

| Secret | Used by | Rotate locally |
|---|---|---|
| `CRIMEMAP_APP_PASSWORD`, `CRIMEMAP_WORKER_PASSWORD`, `CRIMEMAP_AI_PASSWORD`, `CRIMEMAP_MIGRATOR_PASSWORD` | database logins | As superuser `ALTER ROLE <role> PASSWORD '<new>';`, update `.env`, recreate the services that use it |
| `REDIS_PASSWORD` | API, worker, BFF | update `.env`, recreate `redis` and its clients; sessions are lost (users sign in again) |
| `CRIMEMAP_SESSION_KEY` | BFF session encryption | update `.env`, restart the BFF; all sessions end |
| `CRIMEMAP_KC_WEB_CLIENT_SECRET`, `CRIMEMAP_KC_AUDIT_SYNC_SECRET` | BFF, worker | regenerate in Keycloak (client credentials), update `.env`, restart |
| Keycloak signing keys | tokens | add a new key in the realm, make it active, remove the old one after the token lifetime; the API refreshes its JWKS cache on an unknown key id (rate-limited) |
| `CRIMEMAP_DEMO_SIGNING_KEY` | demo batch HMAC | new key in `.env`; re-sign demo batches with the generator; old files are then rejected |
| `CRIMEMAP_METRICS_TOKEN` | API, Prometheus | update `.env` and `secrets/metrics-token` together, recreate `api` and `prometheus` |
| `CRIMEMAP_BACKUP_PASSPHRASE` | backups | new passphrase for new backups; keep the old one in escrow until every backup made with it has expired |
| Demo user password, TOTP keys | demo sign-in | Keycloak admin console (Users → Credentials) |

After a rotation, check `/api/v1/ready`, a sign-in, and the next scheduled job run.

## 7. Routine tasks

| Task | How | When |
|---|---|---|
| Restore test | §4.2 | after each kept backup; monthly |
| Review alerts and thresholds | Prometheus alerts page, Grafana dashboard | monthly |
| Dependency and image scans | `scripts/run_security_checks.sh` | every change (CI) and weekly |
| Audit-exception review | [security-audit-checklist.md](security-audit-checklist.md) §5 | by each exception's review date |
| Review AI insights left pending | Review Queue → AI insights | weekly |

## 8. Production gaps (not built)

- No Alertmanager or on-call routing; no log aggregation (alerts on log events such as
  `audit_chain_broken` need one).
- No OpenTelemetry tracing: the request ID is the correlation key.
- No high availability or automatic failover; one scheduler process by design.
- Point-in-time recovery, key-management service and offsite backups are production requirements
  (§4.4).
