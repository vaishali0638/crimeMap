# Phase 5 Delivery Report — Analytics Workbench and Controlled Exports

**Date:** 2026-10-12 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §8.

> **Audited 2026-10-12** — see [phase-5-audit.md](phase-5-audit.md). The audit fixed privacy defects:
> chart buckets could be differenced against totals to reveal small sensitive groups, and evidence
> snapshots (including comparisons) could be read without the access that produced them. It also
> fixed station-scoped snapshots and exports, and about 30 smaller issues (migration 0009). Current
> totals: 489 Python, 98 web tests.

Nothing has been committed or pushed: the repository has no commits and no remote, so CI has not run
on GitHub. Every CI step was run locally (§4). CRIMEMAP is **not** claimed to be production ready.

> **Phase 4 audit: not finished.** The Phase 4 audit was started but cut short when Phase 5 began.
> - The web review reported 12 findings. **None has been fixed yet.** The most important: the PY
>   Dashboard map re-fits to the station on every re-render; focus is lost after View Details;
>   errors are generic.
> - The backend review stopped early (usage limit), so the dashboard API has **not** been audited.
> - See §7.

## 1. Specification acceptance criteria

| Criterion (spec Phase 5) | Result | Evidence |
|---|---|---|
| Every analytics result displays filters, record count, excluded records, freshness, method and caveat | **Met** | Every section returns the api.md §2 envelope. The analysis header shows title, explanation, active filters, selected and excluded counts with reasons, the "not counted" rule, data-quality note, method, freshness and classification. Caveats are listed under every result. `test_every_result_shows_filters_counts_freshness_method_and_caveats` (5 sections, audited). Browser: all sections as `demo.supervisor`. |
| The occurrence/report time toggle changes applicable views correctly | **Met** | `test_time_basis_toggle_changes_the_applicable_views`: counts equal SQL on each basis; series and day × hour differ; the caveat appears. Browser: switching to report time changed Location Trends from 4,182 to 4,302 records, and the header says "time basis reported". |
| No chart accidentally mixes incompatible record types | **Met** | CASE by default. "All record types (combined)" must be chosen and adds a caveat. Case status applies to CASE only. `test_record_count_is_cases_only_unless_combined_is_chosen` (counts against SQL). Mutation-checked. |
| Export includes watermark and metadata | **Met** | CSV and PDF carry `INTERNAL / RESTRICTED` and the metadata: requesting user, date/time, filters, classification, source and refresh time, data quality, method, evidence snapshot, audit and expiry policy. The PDF watermark is on every page. Exports need permission, a declared purpose and a fresh sign-in; refusals are audited. Files are requester-only and expire after 24 hours. Tests: `test_exports_need_permission_…` and `tests/unit/test_reports.py` (CSV formula neutralisation, PDF structure). macOS PDFKit opened a generated briefing: 3 pages, watermark on each. Browser: re-auth refusal → "Sign in again" → PDF generated and fetched. |

AN-1 to AN-5 ([acceptance-test-plan.md](acceptance-test-plan.md)) are covered by those tests. AN-4
(density thresholds) is in `test_location_trends_state_unit_method_and_unavailable_views`.

## 2. What was built

| Area | Delivered |
|---|---|
| Schema (migration `0008_analytics`) | `map_selection` also returns occurrence and report times, confidence and location method. `core.analytics_selection`: locality and IST time-of-day filters, plus the ADR-012 masked flag. New tables `core.evidence_snapshot` (immutable), `core.pattern_review` (DRAFT) and `core.report_export` (requester-only, 24 h). Analyst notes can attach to evidence snapshots. The downgrade is covered by the migration round-trip test. |
| API (`analytics/`, `routes/analytics.py`, `routes/reports.py`, `reports/`) | Five sections:<br>• Overview: KPIs, trend, categories, DQ status, 1 km map, open review items.<br>• Location Trends: hexagon/beat/station counts, repeat locations, persistence, KDE with thresholds, road segments stated as unavailable.<br>• Case Trends: series, by category, day × hour, calendar, delay, case status with access.<br>• Comparison: region, station, beat, period, year, category; side-by-side maps; difference table; rate unavailable.<br>• Data Quality: measures, location method, confidence, open issues, feed health.<br>Also: Civic refused while its flag is off; evidence snapshots with SHA-256; pattern reviews from evidence; CSV/PDF exports (dependency-free PDF writer); the export list and download. Worker: hourly purge of expired exports. |
| Web (`/analytics`, `/reports`) | Seven-item sidebar (Civic disabled, AI Analysis marked Phase 7, steward limited to Data Quality).<br>Unified filter bar: region, station, beat, locality, category, period, time of day, record type, time basis, case status (if permitted), location confidence, comparison period; Apply and Reset; client-side validation.<br>Analysis header, then Map / Chart / Table / Explanation. Apache ECharts (canvas, rich-text tooltips, ARIA). Maps reuse MapCanvas.<br>Export Report (format, purpose, "Sign in again"), Add Analyst Note, Open Evidence, Create Pattern Review.<br>Reports page listing your exports. |
| BFF | Forwards `Content-Disposition`, for export filenames. |
| Docs | ADR-035; API outline, data dictionary, acceptance plan, traceability (R-SCR-3, R-SCR-5, R-API-2, R-SAFE-5, R-UX-2), README. |

## 3. Defects found and fixed during Phase 5

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | Exports were refused with "purpose required": the policy engine (Phase 1) requires a declared purpose for re-auth permissions | integration test | Export requests carry a purpose, which is audited. The UI asks for it. |
| 2 | Pattern review creation returned 500: dates were passed as strings to asyncpg | integration test | Parsed to `date` |
| 3 | The workbench always started on Puducherry, so a supervisor of another region got a refusal | browser test | Starts from the caller's first permitted region |
| 4 | Switching sections crashed the page: the previous section's data was kept as placeholder, with a different shape | browser test | Placeholder data only within the same section |
| 5 | Two privacy and safety tests could not fail (no 1–2 sensitive bucket in the overview; no formula-like value in the CSV) | mutation check | Added a per-day SQL comparison and unit tests with crafted values. Both now catch the mutation. |
| 6 | `assert` in production code (bandit) and `len(result.all())` in the purge job (semgrep) | security scan | Explicit errors; a counting query |
| 7 | The map container (420 px) was smaller than MapCanvas's minimum (480 px) | browser check | Sizes aligned |

Two transient tool failures were retried, not counted as defects: the Trivy database download timed
out, and the ECharts tarball download needed retries.

## 4. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0008 and the round trip) and worker tests | **483 passed** (was 464; +16 analytics integration, +3 export unit tests), 1 deprecation warning from before Phase 3 |
| Mutation checks (Phase 5) | 5 of 5 caught: default CASE record type, per-bucket sensitive threshold, steward DQ-only scope, the person-reference refusal, CSV formula neutralisation |
| ruff, ruff format, mypy --strict (API 51 files, worker 10, as CI runs it) | clean |
| Web: ESLint, tsc, Vitest (**97**, was 93), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` | all PASS (after the two code fixes in §3, #6) |
| New dependency | `echarts` 6.1.0, pinned. It passed the supply-chain policy (release age, provenance) and the audit. |
| Live Compose | Migrated to 0008; API, worker and scheduler rebuilt |
| Browser (`demo.supervisor`, 1440 × 900) | All sections render, including tooltips. 0 CSP violations, no runtime errors after the fixes. Map views draw counts and density. The time-basis toggle works. Export: re-auth refusal → sign in → PDF generated and fetched (`%PDF-1.4`, attachment). A pattern review was created (132 records). 32 resources, all same-origin. |

Test account note: `demo.supervisor` had not enrolled an authenticator. A test TOTP key was enrolled
on the local Keycloak and kept in the session scratchpad, not in the repository or this report.

## 5. Performance (ADR-017)

In-process harness, 2-vCPU Colima VM, PostgreSQL 768 MB, Puducherry, 12 months (40,711 cases),
comparison with the previous period:

| Endpoint | 1 user | 10 concurrent p95 | Target (analytics overview < 1.5 s at 10 VUs) |
|---|---|---|---|
| Overview | 0.36 s | 2.6 s | not met |
| Location Trends (hexagons 500 m + density) | 0.53 s | 3.9 s | not met |
| Case Trends | 0.23 s | 1.5 s | at the margin |
| Data Quality | 0.29 s | 2.7 s | not met |

Each section runs several grouped scans, for the current and the comparison period. Single-user
times are fine; concurrency on two vCPUs is not. This is the same open item as Phases 3–4: options
are pre-aggregation, caching or more CPU. Not k6.

## 6. Security and privacy notes

- Territory and permission are checked per section; denials are audited. A steward's analytics
  access is limited to Data Quality. Aggregate-only roles get aggregates only. Status filters need
  record-summary access.
- Sensitive categories follow ADR-012 per bucket and cannot be filtered individually without access.
  Repeat-location lists never include them.
- Exports:
  - permission, purpose and a sign-in within 5 minutes are all enforced by the policy engine;
  - aggregated tables only; watermarked; CSV formula injection neutralised;
  - requester-only (row-level security); expire after 24 hours; deleted by the worker;
  - request, generation and download audited.
- Pattern reviews refuse personal identifiers and references to individuals, and store no
  person-level fields.
- Charts render tooltips as canvas text: no HTML from data, no inline styles under the CSP.

## 7. Known limitations and open items

| Item | Status |
|---|---|
| **Phase 4 audit**: 12 web findings unfixed (map re-fit on every render; focus loss after the details drawer; generic errors; date inputs firing per keystroke; URL region/station mismatch; "All Regions" counts covering one region; no keyboard list of dashboard map features; legend not dashboard-specific; others low). The backend audit was not run. | **open** (recommended before Phase 6) |
| Analytics concurrency (§5) | open |
| Map-image export (PNG/PDF) | not built: needs server-side map rendering (ADR-035) |
| Exports stored in PostgreSQL, not the S3 object store (ADR-021); download through the authenticated API instead of signed URLs | deviation documented (ADR-035) |
| Civic Trends | disabled behind its flag (no verified civic source) |
| AI Analysis | Phase 7 |
| Pattern review decisions (submit, approve, return, reject) | Phase 6 |
| Analytics map views show cells only (no region outline) | minor |
| No Playwright, axe or visual tests; the UI was checked by hand | open |
| Carried over: Phase 3 audit residuals, the F1 decision from the Phase 2 audit, `demo.analyst` TOTP, the dev-only `braces` advisory | open |

## 8. Go / no-go

**Conditional go for Phase 6.**
- All four Phase 5 acceptance criteria are met with tests and a browser check.
- Tests, linters, types, the build and the security scans are clean.

The condition: **finish the Phase 4 audit first** (fix the 12 web findings and run the dashboard API
audit). Then decide on pre-aggregation for concurrency.
