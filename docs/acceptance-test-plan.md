# Definition of Done and Acceptance Test Plan

> Phase 0 plan. No tests exist yet. Test results will be recorded per phase in the phase delivery
> report with the exact command and output; nothing is reported as passing without evidence.

## 1. Definition of Done (project)

CRIMEMAP is "done" only when all ten specification conditions hold, each with evidence:

| # | Condition | Evidence |
|---|---|---|
| 1 | Demo/Sandbox mode works end-to-end with synthetic records | E2E scenarios 1–10 pass in CI |
| 2 | Home Map, PY Dashboard, Analytics work as specified | Screen acceptance tests (section 4) |
| 3 | RBAC/ABAC enforced on backend APIs and data queries | Generated authorisation matrix tests + RLS tests |
| 4 | Every view/export/write decision is audited | Audit assertions in integration and E2E tests |
| 5 | DQ, review, action, outcome workflows work end-to-end | Scenarios 4–7, 10 |
| 6 | AI explainable, human-reviewed, policy-limited, can be disabled | Scenario 8; kill-switch test; AI policy tests |
| 7 | All tests and CI gates pass | CI run link and summary |
| 8 | Documentation complete | Doc checklist in README |
| 9 | Security and privacy controls implemented and tested | Security test suite (section 6) + scan reports |
| 10 | No integration claimed or enabled without approval | Adapter gate test; integration-contracts.md |

"Production ready" is **not** claimed by this project; additional external prerequisites apply (see
[threat-model.md](threat-model.md) §9).

## 2. Definition of Done (per phase)

A phase is complete when: its acceptance criteria pass with recorded evidence; unit + integration tests
for new code pass; scans show no unresolved critical/high issue (or a documented risk acceptance);
relevant docs are updated; and a delivery report lists files changed, commands, test results,
limitations, and the next-phase plan.

## 3. Test layers and tools

| Layer | Tool | Scope |
|---|---|---|
| Backend unit | pytest | policy engine, services, mappings, state machines, suppression, geospatial helpers |
| Backend integration | pytest + Testcontainers (PostGIS 16, Redis 7) | repositories, RLS, migrations, ingest pipeline |
| Property-based | Hypothesis | ingest idempotency, filter/scope intersection, suppression non-recoverability |
| API contract | Schemathesis against generated OpenAPI | schema conformance, 5xx hunting, `meta` envelope presence |
| Authorisation | Generated pytest matrix (role × endpoint × scope) from the policy table | allow/deny for every cell |
| Frontend unit | Vitest + React Testing Library | components, hooks, form validation |
| E2E | Playwright (+ axe-core) | the ten scenarios + screen acceptance, accessibility |
| Visual regression | Playwright screenshots (synthetic data, fixed seed) | shell, Home Map, PY Dashboard, Analytics overview |
| Performance | k6 | map and analytics endpoints (ADR-017) |
| Security (dynamic) | OWASP ZAP baseline + targeted pytest security tests | section 6 |
| Static / supply chain | semgrep, bandit, eslint security rules, gitleaks, pip-audit, pnpm audit, trivy (fs, image, IaC), syft SBOM | CI gates |

Rules (from test-master and TDD practice): test error paths, not only success; no production data;
tests independent of order; flaky tests are quarantined and fixed, not re-run until green; assert
observable behaviour, not internal calls.

## 4. Screen acceptance tests

### Home Map (Phase 3)
- HM-1 Filters update map features, legend counts, and bottom status bar consistently.
- HM-2 Territory-scoped user cannot query or view another territory's records (API and UI).
- HM-3 Record popup shows only permitted fields per role (field masking table).
- HM-4 Operational record dialog has no FIR option; submitting creates an audit event; types needing
  review enter the queue.
- HM-5 No API route can create or modify an official case record (contract test).
- HM-6 Map makes no requests to third-party origins (network assertion).

Phase 3 evidence ([phase-3-report.md](phase-3-report.md)): HM-1 to HM-5 are integration tests in
`services/api/tests/integration/test_home_map.py`. HM-6 and the UI side of HM-1/HM-2/HM-4 were checked
by hand in the browser. They are not yet Playwright tests. The Phase 3 audit added
`test_phase3_audit.py` (privacy, review and database-rule regressions) and re-checked HM-6 (39
resources, all same-origin).

### PY Dashboard (Phase 4)
- PD-1 Region filter updates station list, map, and Trends consistently.
- PD-2 Station click fits map to the correct geometry and highlights the correct tags.
- PD-3 View Details shows counts matching the API and offers View Station on Map and Back.
- PD-4 All seven Trends boards show period, category, time basis, scope, count/rate definition,
  data-quality caveat, refresh time, and the export/logging restriction notice.
- PD-5 Police Station Crime Rate board shows "Rate unavailable" when no approved denominator exists.
- PD-6 No prohibited wording ("dangerous", "worst", "criminal") appears anywhere (text scan).

Phase 4 evidence ([phase-4-report.md](phase-4-report.md)): PD-1, PD-3, PD-4, PD-5 and PD-6 are
integration tests in `services/api/tests/integration/test_dashboard.py`. PD-6 is also scanned in the
web catalogue (`apps/web/tests/dashboard-query.test.ts`). PD-2 and the UI side of PD-1 and PD-3 were
checked by hand in the browser. None of them is a Playwright test yet.

### Analytics (Phase 5)
- AN-1 Every result displays filters, record count, excluded count, freshness, method, caveat.
- AN-2 Occurrence/report time toggle changes applicable views.
- AN-3 Incompatible record types are never mixed unless combined type is chosen, with caveat.
- AN-4 KDE layer is unavailable below configured count/quality thresholds, with explanation.
- AN-5 Export includes watermark and all required metadata.

Phase 5 evidence ([phase-5-report.md](phase-5-report.md)): AN-1 to AN-5 are integration tests in
`services/api/tests/integration/test_analytics.py`, with export unit tests in
`services/api/tests/unit/test_reports.py`. The workbench UI, export, evidence and pattern-review
flows were checked by hand in the browser.

## 5. Required end-to-end scenarios

| # | Scenario | Key assertions |
|---|---|---|
| E2E-1 | Demo analyst logs in, filters Home Map, clicks a tag, opens permitted details, adds an analyst note | Note saved; `RECORD_VIEW` and `DATA_CHANGE` audit events with the request ID |
| E2E-2 | Station officer sees only assigned station data | Other stations absent from list/map/search; direct API call to another station → 403/404 + `AUTHZ_DENIED` audit |
| E2E-3 | Region supervisor selects region → station → View Details → Trends | Counts consistent across views; boards show definitions |
| E2E-4 | Analyst creates a pattern review from evidence | Creation impossible without evidence fields; snapshot ID stored |
| E2E-5 | Supervisor approves / returns / rejects | Each transition audited; analyst cannot decide their own review; officer cannot approve |
| E2E-6 | Officer records action completion | Only assigned action; evidence required |
| E2E-7 | Analyst completes outcome review | Cannot finalise without baseline and evidence references |

Phase 6 status: E2E-4 to E2E-7 are covered by API integration tests (`tests/integration/test_workflow.py`)
and a browser check as `demo.supervisor` (decide, propose, approve, outcome create and finalise); the
officer's completion step was not run in the browser (no KKL officer account). Not Playwright.
| E2E-8 | AI creates an insight; analyst validates or dismisses | Card shows evidence/limitations/label; decision audited; AI has no write path |

Phase 7 status: E2E-8 is covered by `tests/integration/test_ai.py` (API and database) and a browser
check as `demo.steward` (validate a duplicate suggestion: confirmation, flash, history). Not Playwright.
| E2E-9 | Unauthorised user attempts export | Denied; `AUTHZ_DENIED` audited; authorised user must re-authenticate |
| E2E-10 | DQ issue flows from ingest to review to resolution | Injected defect detected by rule, appears in queue, resolved, audited |

## 6. Security test cases

| Area | Tests |
|---|---|
| Authentication bypass | Missing/expired/forged/wrong-audience tokens rejected; MFA claim required for case roles |
| IDOR / scope escape | Every resource endpoint with out-of-scope IDs; filter tampering; bbox outside scope |
| RLS | Direct DB queries as `crimemap_app` with different scope settings; owner-bypass check |
| Injection | SQL meta-characters in all string filters (parameterisation); Schemathesis fuzz |
| XSS | Script payloads in notes/titles rendered inert; CSP header present and strict |
| CSRF | State-changing BFF requests without token or with foreign Origin rejected |
| SSRF | No endpoint fetches user-supplied URLs; renderer has no network egress |
| Rate limiting | Burst above limit → 429 with Retry-After |
| Uploads | Wrong MIME, mismatched magic bytes, oversized file, EICAR test string → rejected/quarantined |
| Suppression | Small-cell counts hidden; subtraction attack does not recover them |
| Exports | Watermark present; signed URL expires; link unusable from another session |
| Audit integrity | UPDATE/DELETE on audit table fails; hash-chain verification detects tampering; a denied or failing request whose main transaction rolls back still leaves its `AUTHZ_DENIED`/error audit event |
| Purpose binding | Export or step-up action without a declared purpose is rejected; purpose not permitted for the role is rejected; purpose recorded in audit |
| Request correlation | One `request_id` appears in BFF log, API log, audit event, and any worker job it triggers |
| Secrets | gitleaks clean; no secrets in image layers (trivy) or frontend bundle |

## 7. Performance and accessibility

- k6: map features p95 < 500 ms and analytics overview p95 < 1,500 ms at 200,000 records, 10 VUs
  (ADR-017, proposed). Results reported with hardware details.
- Accessibility: automated token contrast test (every text/background and control/background token
  pair used by the design system meets WCAG 2.2 AA: 4.5:1 text, 3:1 large text and non-text);
  axe-core zero serious/critical violations on all main screens; manual keyboard-only
  walkthrough of scenarios 1 and 3; screen-reader spot check (VoiceOver); results in `accessibility.md`.
