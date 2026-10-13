# Phase 8 Delivery Report — Hardening, Performance, Accessibility and Operations

**Date:** 2026-10-13 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §9.

> **Audited 2026-10-13**: see [phase-8-audit.md](phase-8-audit.md). The audit fixed the backup tool
> (a failed restore left a plaintext dump and blocked later restores; a failed dump was reported as
> written), unbounded metric labels, secrets Prometheus and Grafana could not read, a weak SSRF
> test, and three documentation errors. Current totals: 632 Python, 121 web unit and 31 Playwright
> tests.

Nothing has been committed or pushed, so CI has not run on GitHub; every check was run locally (§6).
CRIMEMAP is **not** claimed to be production ready.

## 1. Specification acceptance criteria

| Criterion (spec Phase 8) | Result | Evidence |
|---|---|---|
| Map filter requests p95 under [TARGET_MS] for [TARGET_RECORD_COUNT] indexed demo records (ADR-017 proposes 500 ms, 200,000 records, 10 concurrent users, uncached) | **Not met** | 199,773 records. At 10 concurrent users p95 is 554–2,949 ms for map queries and 3,024 ms for the analytics overview (target 1,500 ms). With one user, every map query except 24-month 500 m hexagons (607 ms) is under 500 ms. The database is CPU-bound on the 2-vCPU VM (§3). |
| Test suite, scans and accessibility checks pass | **Met** | 627 Python tests, 121 web unit tests, 31 Playwright tests (9 scenario tests covering required scenarios 1–10, plus a CSRF check, 18 accessibility, 3 visual, 1 smoke); all six security scans; axe finds 0 WCAG 2.2 A/AA rule violations on 11 screens (§6). A full run on an idle machine passed 31 of 31. One earlier run, while the Python suite was also running, timed out in one visual test. |
| Restore test succeeds | **Met** | Live: encrypted backup of the 1.2 GB database (85 MiB), restored into a scratch database, 871,589 rows in 36 tables, the audit chain, RLS and policies matched. Automated in `test_backup_restore.py`, including a wrong key and tampered files (§4). |
| No critical/high unresolved vulnerability without documented risk acceptance | **Met, with one acceptance for the owner to confirm** | One high advisory, dev-only (`braces`, lint tooling; no fix published): RA-001 ([security-audit-checklist.md](security-audit-checklist.md) §5). The production audit is clean. |

## 2. Deliverables

| Spec item | Delivered |
|---|---|
| Load/performance tests | `services/api/scripts/bench_map.py` (in-process harness, 13 map, dashboard and analytics queries; `--only` filter added). Results in §3 and ADR-017. k6 was not used: not installed, and the running API accepts only Keycloak tokens. |
| Penetration-test remediation checklist | [pentest-remediation-checklist.md](pentest-remediation-checklist.md): preparation, areas mapped to the OWASP Top 10, fix times, sign-off, internal findings register |
| Security audit checklist | [security-audit-checklist.md](security-audit-checklist.md): 30 controls with status and evidence, risk acceptances, scan results |
| Backup/restore test | `scripts/backup_restore.py` (backup, restore-test), ADR-039, `test_backup_restore.py` |
| Disaster-recovery runbook | [runbook.md](runbook.md) §4–§6: backup, restore test, real restore, DR scenarios, key rotation |
| Observability dashboards and alerts | `/metrics` (ADR-038); Prometheus config, 15 alert rules, Grafana dashboard (16 panels) under `infrastructure/observability/`; Compose profile `observability` |
| Accessibility audit | [accessibility.md](accessibility.md); `apps/web/e2e/accessibility.spec.ts` |
| User/admin/operator documentation | [user-guide.md](user-guide.md), [admin-guide.md](admin-guide.md), [runbook.md](runbook.md), [incident-response.md](incident-response.md) |
| Deployment documentation and environment promotion policy | [deployment.md](deployment.md) |
| Also (spec documentation list) | [analytics-methods.md](analytics-methods.md) |
| Also (spec QA) | Playwright E2E for all ten required scenarios; authorisation sweep for each role; visual regression; security tests (§5) |

## 3. Performance

In-process harness against the live Compose database as the application role (row-level security
on), 2-vCPU / 4 GB Colima VM, PostgreSQL limited to 768 MB, uncached, p95 in ms:

| Query | 1 user | 10 users (before) | 10 users (after `jit=off`) |
|---|---|---|---|
| Hexagons 500 m, Puducherry, 24 months | 607 | 3,334 | 2,949 |
| Hexagons 250 m, Puducherry, 90 days | 240 | 1,890 | 1,846 |
| Beat totals, 12 months, one category | 94 | 600 | 554 |
| Points, one station, 90 days | 142 | 591 | 554 |
| Points, 30 days, view box | 111 | 668 | 689 |
| Station details, 90 days | 14 | 159 | 179 |
| Trends: top spot, all regions, 12 months | 1,116 | 5,403 | 6,419 |
| Analytics overview, 12 months | 637 | 3,506 | 3,024 |
| Analytics location (hexagons), 12 months | 730 | 5,061 | 4,868 |

- **Bottleneck.** The database used 170–195 % CPU (both vCPUs) during the 10-user runs; the harness
  process used under 80 % of one host core. An `EXPLAIN ANALYZE` of the 24-month selection shows
  185 ms for the scoped join of 107,000 incidents and their locations before any aggregation.
- **Change made.** `jit=off` in the Compose PostgreSQL command. One A/B run of the slowest query:
  2,975 to 2,601 ms. Across the full suite the difference is within run-to-run noise (some queries
  were slower), so the gain is small and uncertain.
- **Not done.** Pre-aggregated cells, vector tiles or a response cache (ADR-017 options): each changes
  how masking and scope are applied and needs its own design and tests. The targets stay "proposed";
  the owner should confirm them and decide whether to fund the remediation or more database CPU.
- Measurements are noisy: other work on the host (builds, tests) changes results by 10–30 %.

## 4. Operations

- **Metrics** (`services/api/src/crimemap_api/metrics.py`): request counter and latency histogram by
  route template, and 11 audit-derived signals. Its own bearer token; 404 without one. Live check:
  401 without the token; with it, `crimemap_signals_up 1` and all signals present.
- **Alerts**: availability (API down, signals unavailable, error rate), performance (map and analytics
  p95 against ADR-017), security (all six spec alerts) and data (ingestion, stale source, AI
  failures). Each links to a runbook or incident-response section; a unit test checks the metrics
  and the links.
- **Backups**: GnuPG AES-256 inside the database container, manifest in the same snapshot,
  restore test into a scratch database. Live and automated runs passed.
- **Secrets**: `dev_bootstrap.sh` now adds `CRIMEMAP_METRICS_TOKEN`, `CRIMEMAP_BACKUP_PASSPHRASE`,
  the AI password if missing, `secrets/metrics-token` and `secrets/grafana-admin-password` (never
  printed). The local `.env` received the two new keys.
- **Not run**: Prometheus and Grafana (images not pulled in this environment; `compose config`
  validates, the rule/dashboard test passes). No Alertmanager, log aggregation or tracing.

## 5. Security testing

New tests: `tests/integration/test_security.py` (36), `test_metrics_signals.py` (3),
`test_backup_restore.py` (5), `test_phase8_fixes.py` (2), `tests/unit/test_metrics.py` (10),
`test_observability.py` (4), `test_malicious_batches.py` (12), plus one adapter test.

| Spec security test | Coverage |
|---|---|
| Auth bypass | every non-public route refuses anonymous callers; 10 forged or unacceptable token kinds refused and audited |
| Authorisation for each role | all 9 roles × every permissioned route: refused (403 + `NO_PERMISSION` audit) exactly when the role lacks the permission |
| IDOR, scope escape | other station's record and guessed ids answer 404; region and station filters outside scope refused |
| Injection | 7 payloads × 7 probes: no 500, values not echoed in errors, no data change |
| XSS | stored markup returned as inert JSON with `nosniff` and CSP `default-src 'none'` |
| CSRF | API ignores cookies; BFF refuses without the token (E2E) |
| SSRF | no endpoint accepts a URL or host; JWKS client follows no redirects |
| Rate limit | per-user limit answers 429 with `Retry-After` |
| Malicious upload | oversized, malformed, non-UTF-8, deeply nested, forged, altered and miscounted batches; symbolic links |

Mutation checks: removing the route permission check fails 9 tests; echoing input in validation
errors fails 7.

## 6. Verification (run locally, 2026-10-13)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0013 and round trip) and worker tests | **627 passed** (was 554), 1 deprecation warning from before Phase 3 |
| ruff, ruff format, mypy --strict (API 57 files, worker 11) | clean |
| Web: ESLint, tsc, Vitest (**121**, was 116), `next build` | clean |
| Playwright (installed Chrome, live stack, 7 demo users) | **31 passed** in a full run with fresh sign-ins. An earlier full run, while the Python suite was also running, had one timeout in the Home Map visual test. |
| OpenAPI regenerated vs committed copy | in sync (`/metrics` is outside the document) |
| `scripts/run_security_checks.sh` | all PASS after two fixes in new Phase 8 files (gitleaks: test vector; semgrep: false positives annotated) |
| `scripts/check_docs.py` | no problems |
| Live Compose | PostgreSQL recreated with `jit=off`; API, worker, scheduler and AI worker rebuilt and restarted; `/api/v1/ready` 200 |

## 7. Defects found and fixed in Phase 8

| # | Defect | Found by | Fix |
|---|---|---|---|
| 1 | Deeply nested JSON batch raised `RecursionError` (filed FAILED, no rejection audit, so no alert) | malicious-upload tests | rejected as malformed |
| 2 | Batch drop zone followed symbolic links | malicious-upload tests | regular files only |
| 3 | Region steward could list a source-level DQ issue but its History answered 404 | E2E exploration | same rule as the queue |
| 4 | Search box `aria-controls` referenced a missing element (axe critical) | axe | reference only while rendered |
| 5 | Home Map, PY Dashboard and Analytics scrolled horizontally at 320 px (WCAG 1.4.10) | reflow test | panels stack below `md`; header toolbar wraps |
| 6 | Workflow hints not linked to the disabled buttons they explain | code review | `aria-describedby`; E2E asserts the description |

## 8. Known limitations and open items

| Item | Status |
|---|---|
| Performance targets (ADR-017) | **not met**; owner decision on targets and remediation (§3) |
| RA-001 (`braces`, dev-only) | accepted pending owner confirmation; review by 2026-11-13 |
| Prometheus/Grafana not run; no alert routing, log aggregation or tracing | open |
| Real restore (database swap, runbook §4.3) not exercised | open |
| Retention policy jobs (audit 180 days, AI runs and insights, records by classification) | **not built** (R-SEC-3, R-PRIV-2) |
| Kubernetes/OpenTofu reference manifests | not built (no hosting platform chosen) |
| Manual accessibility checks (screen readers, text resize, target size) | not done ([accessibility.md](accessibility.md) §5) |
| External penetration test | not done |
| Analytics-methods review found code and ADR wording that disagree: dashboard boards count all record types, while ADR-036 says CASE counts; a STATION comparison area in outcome reviews counts records without usable locations; ADR-012's "configurable" small-cell threshold is a constant | open — documented as coded in analytics-methods.md; owner to decide the intended rule |
| No user, grant, audit or boundary-approval screens (API only); station-scoped grants cannot be created through the API | open (admin-guide.md) |
| Carried over: Phase 4 audit, 23 duplicate pending pattern cards (Phase 7), duplicate-detector run time | open |

**Demo data written by this phase.** The E2E runs act as real users on the live demo database:
- they added notes, pattern reviews, actions, completions and outcome reviews (texts start with
  "E2E");
- they validated AI insights and resolved data-quality issues;
- the load test wrote `MAP_QUERY` and `ANALYTICS_QUERY` audit events as `demo.analyst`.

Global setup also enrolled authenticators for `demo.officer`, `demo.sho`, `demo.admin` and
`demo.auditor`, which had never enrolled. Their keys are in the git-ignored
`secrets/demo-totp-keys.json`, alongside those of the analyst, steward and supervisor.

## 9. Go / no-go

**No-go for production.** That was expected; Phase 8 shows what remains.

**Conditional go for a Phase 8 audit and a supervised demonstration.**
- Three of the four Phase 8 acceptance criteria are met: tests, scans and accessibility checks; the
  restore test; and vulnerabilities, with one documented acceptance.
- All ten required end-to-end scenarios pass in a real browser.

Conditions:
- The owner confirms or revises ADR-017 targets and RA-001.
- Run Prometheus and Grafana once and confirm the alerts fire.
- Decide the retention policy.
