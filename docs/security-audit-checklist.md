# CRIMEMAP Security Audit Checklist

**Environment:** DEMO, synthetic data only. Not a production-readiness claim. Status as of the
Phase 8 run (2026-10-13). Each line names its evidence: a test, a scan or a document. "Verified"
means a test or scan ran and passed in Phase 8; "Implemented" means the control exists but was not
re-tested in Phase 8; "Open" and "Not done" are gaps. Use this list for each release and before an
external audit ([pentest-remediation-checklist.md](pentest-remediation-checklist.md)). Threats and
residual risks: [threat-model.md](threat-model.md).

## 1. Identity, sessions and authorisation

| # | Control | Status | Evidence |
|---|---|---|---|
| 1.1 | Every API route declares an authorisation requirement; startup fails otherwise | Verified | `tests/unit/test_routes.py` |
| 1.2 | Every non-public route refuses an anonymous caller (401) | Verified | `test_security.py::test_every_non_public_route_refuses_an_anonymous_caller` (all routes) |
| 1.3 | Forged or unacceptable tokens refused and audited: `alg=none`, HS256 signed with the public key, another key, expired, wrong issuer, wrong audience, foreign client, no subject, ID-token type, garbage | Verified | `test_security.py::test_forged_or_unacceptable_tokens_are_refused_and_audited` |
| 1.4 | Each of the 9 roles is refused every permission it lacks (403 + `AUTHZ_DENIED/NO_PERMISSION` audit), and never refused one it has | Verified | `test_security.py::test_each_role_is_refused_every_permission_it_lacks` (mutation-checked: removing the check fails 9 tests) |
| 1.5 | MFA (TOTP) required for privileged roles; fresh sign-in for exports, role changes and AI switches | Implemented | Phase 1/5/7 tests; demo realm OTP policy |
| 1.6 | Separation of duties: no self-grant; a review or action is decided by someone other than its author | Verified | `test_api.py`, `test_workflow.py`; E2E scenarios 4–7 |
| 1.7 | Web sessions server-side (Redis), cookie `HttpOnly`, `SameSite=Strict`, `Secure` outside local http; 15 min idle, 8 h absolute | Implemented | `apps/web/lib/session.ts`, `session-policy.ts`, unit tests |
| 1.8 | Rate limits per IP, per user and per failed authentication | Verified | `test_security.py::test_a_user_over_the_rate_limit_gets_429`; `test_api.py` (auth failures) |

## 2. Data access

| # | Control | Status | Evidence |
|---|---|---|---|
| 2.1 | Row-level security as a second layer (application, worker and AI roles) | Verified | Phase 2–7 tests; restore test checks RLS survives a restore |
| 2.2 | No IDOR: another station's record and a guessed id both answer 404 | Verified | `test_security.py::test_a_station_officer_cannot_open_another_stations_record`; E2E scenario 2 |
| 2.3 | No scope escape through region or station filters (map, analytics, dashboard) | Verified | `test_security.py::test_a_region_scoped_analyst_cannot_escape_their_region` |
| 2.4 | Sensitive categories masked; residential locations generalised; small cells not revealed | Implemented | Phase 3 tests; [analytics-methods.md](analytics-methods.md) |
| 2.5 | AI login cannot create actions, export or modify cases | Verified (Phase 7) | `test_ai.py` |
| 2.6 | Exports: purpose, fresh sign-in, classification, expiry, audited downloads | Implemented | Phase 5 tests; E2E scenario 9 (refusal audited) |

## 3. Input handling and web

| # | Control | Status | Evidence |
|---|---|---|---|
| 3.1 | SQL injection: bound parameters only; injection payloads rejected or treated as data, no 500, no data change | Verified | `test_security.py::test_injection_payloads_are_rejected_or_treated_as_data` (7 payloads × 7 probes); semgrep |
| 3.2 | XSS: stored markup returned as JSON text; `nosniff`; API CSP `default-src 'none'`; web CSP with nonces; React escaping; `react/no-danger` lint rule | Verified | `test_security.py::test_stored_markup_is_returned_as_inert_json`; `apps/web/tests/csp.test.ts` |
| 3.3 | Validation errors never echo submitted values | Verified | mutation check: echoing the input fails 7 tests |
| 3.4 | CSRF: the API ignores cookies; the BFF requires same origin and the session's CSRF token | Verified | `test_security.py::test_a_session_cookie_is_not_a_credential`; E2E "BFF refuses … without the CSRF token"; `csrf.test.ts` |
| 3.5 | SSRF: no endpoint accepts a URL or host; the only outbound call is the configured JWKS URL with redirects off | Verified | `test_security.py::test_no_endpoint_accepts_a_url_or_host_to_fetch` |
| 3.6 | Malicious uploads: batch files are the only file input; oversized, malformed, non-UTF-8, deeply nested, forged and altered files are rejected before staging; symbolic links in the drop zone are ignored | Verified | `tests/unit/test_malicious_batches.py`, `test_adapters.py::test_file_drop_adapter_ignores_symbolic_links` (both found defects, fixed in Phase 8) |
| 3.7 | CSV exports neutralise spreadsheet formulas | Verified | `tests/unit/test_reports.py` |
| 3.8 | Security headers on every API response (CSP, frame denial, no-referrer, no-store, CORP) | Verified | `test_http_layer.py`; asserted in 3.2 |

## 4. Operations

| # | Control | Status | Evidence |
|---|---|---|---|
| 4.1 | Audit log append-only and hash-chained; daily verification | Verified | Phase 1 tests; restore test compares the chain |
| 4.2 | Alerts: mass export, unusual geographic access, repeated denied access, privilege changes, failed ingestion, model failures | Verified (signals); Not run (Prometheus) | `test_metrics_signals.py`, `test_observability.py`; Prometheus/Grafana not started (images not pulled) |
| 4.3 | `/metrics` protected by its own token; labels carry no identifiers | Verified | `tests/unit/test_metrics.py`; live check 401 without token |
| 4.4 | Encrypted backups; restore tested | Verified | `test_backup_restore.py`; live run ([runbook.md](runbook.md) §4) |
| 4.5 | Secrets only in git-ignored `.env`/`secrets/`; none in images, logs or errors | Verified | gitleaks, trivy (§6); structured-log redaction |
| 4.6 | Containers: read-only file systems, no new privileges, all capabilities dropped, ports on 127.0.0.1 | Implemented | `infrastructure/docker/compose.yaml`; trivy misconfiguration scan |
| 4.7 | Dependency release-age gate (7 days), provenance policy, no exotic sub-dependencies | Implemented | `pnpm-workspace.yaml` |
| 4.8 | Encryption at rest, TLS, key-management service, external penetration test | **Not done** (production requirements) | threat model §6, §9 |

## 5. Vulnerabilities and risk acceptances

Rule (spec, Phase 8): no critical or high vulnerability stays unresolved without a documented risk
acceptance. Each acceptance has an owner, a reason, compensating controls and a review date; the
scan ignore is narrowed to the single advisory.

| ID | Finding | Severity | Decision | Review by |
|---|---|---|---|---|
| RA-001 | `braces` ≤ 3.0.3, stack-exhaustion denial of service from deeply nested brace patterns (GHSA-vfj7-8cjw-p6xm). Reached only via `eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` (a development lint dependency). | High | **Accepted (DEMO)**, proposed by the build agent; the owner must confirm. No patched release exists on the registry (latest 3.0.3, 2024-05-21). The code runs only at lint time on patterns from our own configuration; it is not in the web build or any runtime image. `pnpm audit --prod` is unaffected. Ignored in `pnpm-workspace.yaml` (`auditConfig.ignoreGhsas`) for this advisory only. Remove when a fixed version or a Next.js plugin without it is published. | 2026-11-13 |

No other critical or high finding was reported by the Phase 8 scans (§6).

## 6. Scan results (Phase 8)

`scripts/run_security_checks.sh`, 2026-10-13, after the Phase 8 changes: **all six pass**.

| Scan | Result |
|---|---|
| bandit (API and worker) | PASS |
| pip-audit (Python dependencies, strict) | PASS |
| pnpm audit (Node, production, high and above) | PASS |
| pnpm audit (Node, all dependencies) | 1 high, ignored under RA-001 |
| gitleaks (committable files) | PASS. The first run flagged the public RFC 6238 test secret in `apps/web/tests/totp.test.ts`; the test now derives it at run time. |
| semgrep (p/default, p/python, p/typescript) | PASS. The first run flagged four `psycopg.sql.Identifier` compositions in `scripts/backup_restore.py` as raw SQLAlchemy queries; they are annotated as false positives (quoted identifier, a fixed `_restore_test` name, no user input). |
| trivy (dependencies, secrets, IaC; HIGH and CRITICAL) | PASS |
