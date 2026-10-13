# Phase 8 Audit — Hardening, Performance, Accessibility and Operations

**Date:** 2026-10-13 · **Environment:** DEMO, synthetic data only. Not a production-readiness claim.

**Scope.** Every Phase 8 source file and configuration:
- `metrics.py`, the HTTP-layer hook and `metrics_token`;
- the DQ-history fix, the batch-signing and adapter fixes;
- `scripts/backup_restore.py` and `scripts/dev_bootstrap.sh`;
- Compose (`jit=off`, Prometheus, Grafana, secrets) and `infrastructure/observability/`;
- the Phase 8 tests (Python and Playwright);
- the web accessibility and layout changes;
- the Phase 8 documents.

Checked against `crimemap_master_build_prompt.md`: Phase 8, Quality Assurance, Security, UX and
Documentation Deliverables.

**Method.**
- Code review.
- Probes in containers (as the Prometheus and Grafana users).
- A failing regression test for each code defect before the fix (each was seen failing).
- A recheck of every file path and source reference cited in the new documents.
- Live checks on the Compose stack.

Two independent review agents and a documentation-verification agent were started, but all three
stopped on an API rate limit before reporting. Their reviews are **blocked**, and this audit is a
single reviewer's.

| | Rows below |
|---|---|
| Fixed | 11 |
| Open | 4 |
| Blocked | 3 |

## 1. Findings fixed

| # | Severity | Finding (proved) | Fix | Test |
|---|---|---|---|---|
| A1 | **Medium** | Prometheus (uid 65534) and Grafana (uid 472) could not read their secrets. Compose mounts file secrets as-is, so they appeared as `root 0600`, and Compose ignores `uid`/`mode` outside swarm (warning shown in a probe). The observability profile would have failed to scrape (Prometheus) and to start (Grafana). | `dev_bootstrap.sh` makes these two files 0644. `secrets/` stays 0700, so they remain private on the host. | Probe as each container user: unreadable before, readable after |
| A2 | **Medium** | The restore test decrypted to a fixed `/tmp/r.dump` inside the database container and deleted it only after a successful `pg_restore`. A failed restore left a plaintext dump behind, and every later restore then failed (gpg refuses to overwrite). Reproduced: after the tampered-file test, a good restore failed. | Private `mktemp` file, `umask 077`, removed by `trap` on every exit | `test_a_failed_restore_leaves_no_plaintext_and_does_not_block_the_next` |
| A3 | **Medium** | The backup ran `pg_dump \| gpg` under `sh` without `pipefail`. A failed `pg_dump` returned exit 0, so a truncated backup would have been reported as written. | Runs under `bash` with `set -o pipefail` | `test_a_dump_failure_fails_the_backup_pipeline` |
| A4 | **Medium** | The metrics `method` label took any HTTP method token. Any client, before authentication, could grow the registry without bound (one series per invented method). | Methods outside the standard seven are labelled `OTHER` | `test_unknown_http_methods_share_one_label` |
| A5 | Low | Signal values were formatted with `%g`: 1,234,567 was exported as `1.23457e+06` (lossy). | Integers exported exactly | `test_large_counts_are_exported_exactly` |
| A6 | Low | The operator CLI (`ingest_directory`) still followed symbolic links; the inbox fix covered only the scheduled adapter. | Regular files only | `test_ingest_cli_ignores_symbolic_links` |
| A7 | Low (test gap) | The SSRF test inspected only schemas whose names end in In/Body/Request/Query. It missed `IssueUpdate`, `ReviewEdit`, `OutcomeEdit` and every nested model (for example `AnalyticsQuery`). | Walks every request body and parameter through `$ref` recursively; asserts that those models were reached | `test_no_endpoint_accepts_a_url_or_host_to_fetch` |
| A8 | Low | The restore-test report was written with default permissions, then changed to 0600. | Created 0600 | live run (`-rw-------`) |
| D1 | Low (docs) | `pnpm --filter @crimemap/web e2e -- <file>` (accessibility.md) fails: pnpm passes `--` on to Playwright. | Command without `--` | ran both forms: the old one fails, the new one lists 18 tests |
| D2 | Low (docs) | deployment.md's demo-data command omitted `--profile app` (the job depends on `migrate`). | Same command as the README | — |
| D3 | Low (docs) | analytics-methods.md cited abbreviated paths (`api/…`, `worker/…`, `migrations/…`) without saying what they stand for. | Legend added. Every cited file and function reference was checked and resolves. | script check |

## 2. Checked and found sound

- **Metrics:**
  - the token is compared in constant time;
  - 404 without a configured token, 401 otherwise;
  - not in the OpenAPI document and not reachable through the BFF (it proxies `/api/v1` only);
  - histogram buckets are cumulative, with `+Inf`;
  - route labels are templates, with `unmatched` for unknown paths;
  - a database failure is not cached, and a slow database is bounded by the 5-second timeout;
  - signals hold no identifiers.
- **Alert rules and the Grafana dashboard** use only exported metrics, and every runbook link
  resolves (unit test). There are 15 rules.
- **Backups:**
  - the passphrase goes to `docker exec` by variable name, not argv;
  - the GnuPG integrity check runs before `pg_restore` reads anything;
  - the scratch database name is fixed and checked;
  - files are 0600 in a git-ignored directory.
- **DQ-history fix:** it applies the queue's rule for source-level issues and still requires
  `dq.manage` (an analyst gets 404).
- **E2E:**
  - `e2e/.auth/` (session cookies) and `test-results/` (failure traces) are git-ignored;
  - the TOTP keys file is 0600 and is never printed;
  - the RFC 6238 implementation matches the published vectors;
  - global setup signs in again after enrolment, so MFA-gated grants apply.
- **Hints:** the elements they reference with `aria-describedby` always exist (empty when there is
  nothing to say), and the E2E test asserts both states.
- **Layout:** the changes apply only below the `md` breakpoint. At 1440 px the visual baselines
  still match.
- **Guides against the code:**
  - grant reasons 3–500 characters;
  - AI-switch reasons 10–500 characters;
  - export expiry 24 hours;
  - fresh sign-in 5 minutes;
  - `DEMO_USER` the only MFA-exempt role.

## 3. Open and blocked

| Item | Status |
|---|---|
| Independent review agents (backend/ops, web/E2E, documentation verification) | **Blocked** (stopped on an API rate limit; no findings returned) |
| Prometheus and Grafana actually running, and alerts firing | **Blocked** (images not pulled here; the secret-readability probe and `compose config` pass) |
| Manual screen-reader pass | **Blocked** (needs a person with NVDA or VoiceOver) |
| Performance targets (ADR-017) | **Open**: not met at 10 concurrent users; owner decision |
| RA-001 (`braces`, dev-only) | **Open**: accepted pending owner confirmation, review by 2026-11-13 |
| Retention jobs; real-restore swap not exercised; Kubernetes reference | **Open** (Phase 8 report §8) |
| Code and ADR wording disagree (dashboard record types vs ADR-036; outcome STATION comparison; ADR-012 threshold) | **Open**, outside Phase 8 code; documented as coded in analytics-methods.md |

## 4. Verification (run locally, 2026-10-13, after the fixes)

| Check | Result |
|---|---|
| API unit, integration and worker tests | **632 passed** (was 627; +5 audit regression tests), 1 deprecation warning from before Phase 3 |
| ruff, ruff format, mypy --strict (API 57 files, worker 11); `backup_restore.py` mypy --strict | clean |
| Web: ESLint, tsc, Vitest **121** | clean (no web source changed in the audit; the build from Phase 8 is current) |
| Playwright, full run with fresh sign-ins | **31 passed** |
| `scripts/run_security_checks.sh` | all 6 PASS |
| OpenAPI vs committed copy | in sync |
| `scripts/check_docs.py` | no problems |
| Live Compose | API and worker images rebuilt and restarted; `/api/v1/ready` 200; `/metrics` with token: `crimemap_signals_up 1` |
| Live backup and restore test (revised script) | backup written (86.5 MiB); restore test passed; container `/tmp` empty afterwards; report file 0600 |

## 5. Files changed in the audit

- **Tools and configuration:**
  - `scripts/backup_restore.py`: private temp file, trap, pipefail, bash, report mode;
  - `scripts/dev_bootstrap.sh`: secret modes for Prometheus and Grafana.
- **API:** `services/api/src/crimemap_api/metrics.py` (method label, exact values).
- **Worker:** `services/worker/src/crimemap_worker/ingest/__init__.py` (`ingest_directory` skips
  links).
- **Tests:**
  - `tests/integration/test_backup_restore.py` (+2);
  - `tests/unit/test_metrics.py` (+2);
  - `tests/integration/test_ingest.py` (+1);
  - `tests/integration/test_security.py` (SSRF walk).
- **Docs:**
  - this file;
  - `accessibility.md`, `deployment.md`, `analytics-methods.md`, `phase-8-report.md`, `runbook.md`.
- **Local only (not committable):** `secrets/metrics-token` and `secrets/grafana-admin-password` set
  to 0644 inside the 0700 `secrets/`; a new encrypted backup in `backups/`.
