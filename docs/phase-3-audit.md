# Phase 3 Audit — Home Map

**Date:** 2026-10-12 · **Scope:** every Phase 3 source file and configuration (migration 0005, the
map, incident, search, operational-record and note APIs, territory and policy changes, the Home Map
web app, shell, CSP and proxy, Compose settings, tests and docs), checked against
`crimemap_master_build_prompt.md` (Global shell, Screen 1, Phase 3) and the project's own
data-classification rules. Environment: DEMO, synthetic data only. Not a production-readiness claim.

Method: three independent reviews (data layer, API, web and spec coverage), each finding then
re-checked against the code by hand; fixes written with a regression test, and each key fix
mutation-checked: reverting the fix makes its test fail. Plus my own checks (audit coverage,
packaging, a load re-run and a browser pass).

Counted from the rows of the tables below (a row may group closely related findings, e.g. D2
also covers analyst-note authorship):

| | Rows |
|---|---|
| Fixed (§1: 14, §2: 20, §3: 4) | 38 |
| Partly fixed (§3) | 3 |
| Accepted / documented (§3: 1, §4: 6) | 7 |
| Open, low or known (§4) | 4 |
| Blocked (§4) | 2 |
| **Total** | **54** |

## 1. Security and privacy findings (fixed)

| # | Severity | Finding | Fix | Test (mutation-checked) |
|---|---|---|---|---|
| A1 | **Critical** | Points mode filtered the view box on the **exact** point but returned the 250 m snapped one. A station officer could shrink the box step by step to locate a record exactly. | The box selects by the coordinate the caller receives. An index-friendly widened pre-filter is kept. | `test_a_view_box_cannot_locate_the_exact_point_of_a_snapped_record` ✓ |
| A2 | High | The suppression label was "0" with no sensitive records and "fewer than 3" with 1–2. A narrowed query (beat, one day) became a yes/no test for one sensitive case. | For callers without sensitive access, sensitive records not shown in a group of ≥ 3 are counted nowhere. The label is constant, with no number (ADR-012 revised). | `test_a_tiny_sensitive_selection_is_not_counted_at_all`, `…_by_point_counts`, `test_aggregate_roles_…` ✓ |
| A3 | High | Callers without sensitive access could filter on one sensitive category, so the "combined" bucket became per-category. Masked records were also added unthresholded to the no-location and outside counts. | A sensitive category filter → audited 403, and those categories are not offered in metadata. Masked records are left out of the location buckets. | `test_combined_callers_cannot_filter_on_one_sensitive_category`, `test_sensitive_records_are_not_counted_in_location_buckets` ✓ |
| A4 | High | Operational records leaked sensitive incidents in four ways:<br>• a correction could reference a sensitive (or other-region) incident, giving an existence oracle;<br>• listings showed such records and their exact corrected point to everyone in the station;<br>• all record points were exact;<br>• the map layer was exact too. | The subject must be visible (territory, RLS, sensitive category) and in the record's region; otherwise an audited 404, identical to a missing id. Lists and reviews leave out records about incidents the caller cannot see. Points are exact only for the author and the exact-location scope, otherwise snapped. | `test_records_about_a_sensitive_incident_need_sensitive_access`, `test_a_record_may_only_refer_to_an_incident_in_its_own_region`, `test_record_points_are_snapped_for_callers_without_exact_access` ✓ |
| A5 | Medium | Search locality counts included sensitive records. Subtracting the visible points gave sensitive counts per locality. | Same rule as the map. | `test_search_locality_counts_leave_out_sensitive_records` ✓ |
| A6 | Medium | `/map/operational-records` for another region returned 200 (empty) instead of an audited 403. | `region_allowed` → `refuse`. | `test_records_layer_refuses_other_territory_and_follows_the_period` ✓ |
| A7 | Medium | Cell notes had no territory check (RLS alone hid them). Station officers could not create or see cell notes at all. | A cell belongs to the station holding its centre, and access is checked against it: audited 404 outside, and station officers can use cell notes. `GET /notes` now has a limit and is audited as `NOTE_VIEW`. | `test_cell_notes_belong_to_the_station_holding_the_cell` ✓ |
| A8 | Medium | The audit-log redaction of search text missed common identifier formats: spaced or dashed mobile numbers, dashed Aadhaar-like numbers, and other states' vehicle registrations. `hex_size_m` was missing from `MAP_QUERY`. | Wider patterns, which leave references, unit codes and dates untouched. `hex_size_m` is now audited. | `test_search_offers_categories_and_dates_and_redacts_identifiers` ✓ |
| A14 | Medium | `purpose=DQ_REVIEW` gave a steward exact points for the whole region. Data-classification §6 says "for geocode review". | Under that purpose, exact points apply only to records with an open location DQ issue. The list is looked up once per query (see O3). | `test_steward_gets_exact_points_only_under_the_dq_review_purpose` |
| D1 | High | A race: a location insert or update concurrent with an incident move or reclassification left the location's scope copy stale. A now-HIGHLY_RESTRICTED location could stay readable under the old region. | Migration 0006: the sync trigger reads the incident `FOR SHARE`. This conflicts with the row lock an update takes, so the two serialise. | `test_a_location_write_waits_for_a_concurrent_incident_move` ✓ |
| D2 | High | The database did not enforce review rules. The API role could insert an APPROVED record, forge `created_by` and self-approve, move PENDING → NOT_REQUIRED, or approve without a reviewer. | Migration 0006 adds:<br>• an insert trigger: status from kind, no reviewer, visible same-region subject;<br>• a restrictive policy: `created_by` = session user;<br>• an update trigger: PENDING → APPROVED/REJECTED only, reviewer = session user, `reviewed_at` set;<br>• a status/reviewer CHECK.<br>The same author rule applies to analyst notes. | `test_the_database_starts_records_unreviewed_and_by_the_session_user` ✓, `test_the_database_decides_a_record_once_by_another_session_user` (several layers; see note) |
| D3 | Medium | The API role could insert RULE or AI data-quality issues, squat worker rule codes, or create territory-less source issues. | A restrictive insert policy allows only USER issues about an incident, in its region, with a `USER-` rule code. | `test_the_api_role_can_only_insert_user_flags` ✓ |
| D5 | Low | The foreign key on `subject_incident_id` was an existence oracle at the database level. | Covered by the D2 insert trigger, which runs under RLS. | as A4 |
| D11 | Low | Row-level security territory included grants that need MFA in a session without MFA. Grant validity was already filtered. | `db.py` builds the territory from usable grants only. | full suite |

Note on D2: removing only the "approved or rejected" check is still caught by the reviewer and CHECK
rules (defence in depth), so that single mutation does not fail the test.

## 2. Correctness and reliability (fixed)

| # | Finding | Fix |
|---|---|---|
| A9 | A far-future period (`9999-12-…`) caused a 500 (date overflow). | The period must lie between 2000 and next year (422). Tested. |
| A10 | Hour-level times for station officers were truncated in UTC, so officers saw :30 times. | Truncated in IST. Tested. |
| A11 | Re-flagging an issue after it was resolved gave 409 forever. | Flagging again reopens it. Tested. |
| A12 | Concurrent reviews could surface a database exception as a 500. | Row lock and a conditional update: the second reviewer gets 409. |
| W2 | Drawer state carried over between selections. A half-written note or flag could be saved against the **next** record, and an open details panel fired an unrequested audited detail read. | Panels are keyed by selection. Verified in the browser: a draft note is cleared when moving to another cell. |
| W3 | Switching region unmounted the whole workspace: map and WebGL context recreated, focus lost, dialog and search state lost. | Previous metadata kept while loading. Verified in code only: the browser test account has one region. |
| W4 | The record dialog let region, station and point disagree, kept a point across a region switch, and showed one misleading hint for every 422 or 503. | The region is fixed to the map's region, with stations of that region. The point is cleared on region switch. The API's own explanation is shown (verified: "The map point is outside the selected region"). Unavailable kinds are disabled with the reason. |
| W5 | Picking mode could not be cancelled, and keyboard users could not set a point (a location correction needs one). | Escape and a Cancel button return to the dialog (verified). Latitude and longitude can be typed. |
| W6 | Radix's injected `<style>` (scroll lock) needs a nonce under the strict CSP. | The root layout passes the request nonce to `get-nonce`. Verified: dialog opens, scroll lock applies, 0 CSP violations. The violation was not separately reproduced before the fix. |
| W7 | `CRIMEMAP_MAP_STYLE_URL` was unvalidated. `javascript:` or `data:` made the proxy throw (500 on every page), http was accepted in PRODUCTION, and a style's tile, glyph and sprite hosts were not allowed by the CSP. | `lib/map-config.ts`: https only (http for localhost outside PRODUCTION), an optional `CRIMEMAP_MAP_ASSET_ORIGINS`, and an invalid value turns the basemap off and is logged. Unit-tested. |
| W8 | Map errors were generic: an expired session, a rejected filter and a style that failed to load all looked the same or showed nothing. | Status-specific messages; the API's reason for 422; a message if the style fails. |
| W9 | A metadata error dead-ended the page. | Retry, plus a way back to the first permitted region. |
| W10 | The points view box could exceed ±180°, giving a 422. Every pan was a new audited query. | Clamped and debounced (400 ms). Unit-tested clamp. |
| W11 | The records layer ignored the period, could not be clicked, called unreviewed observations "approved", and showed a dead toggle without point access. | Follows the period, is clickable (record panel), is labelled honestly, and the toggle is disabled with an explanation. |
| W12 | Smaller issues:<br>• "Sensitive (combined): not shown" appeared even with no restriction;<br>• no beat highlight;<br>• no highlight before the map loads;<br>• no Escape or focus return on the drawer;<br>• search results never closed on an outside click;<br>• the time was fixed at dialog mount;<br>• 29 px zoom buttons;<br>• review errors were not distinguished. | All fixed. Escape and focus return verified in the browser. |
| S1 | Stale queued map updates after unmount (dev strict mode). | Queue cleared on cleanup. |
| S2 | Choosing a search result also applied unrelated unapplied filter edits. | Applies on top of the applied filters. |
| O1 | The worker's mypy configuration lacked the pydantic plugin, so the CI type-check step would fail. | Plugin added (`services/worker/pyproject.toml`). |
| O2 | The status bar overlapped the map on short windows (fixed-height section). | The section grows, and the page scrolls. Verified at 1440 × 900. |
| O3 | Found by this audit's own load re-run: the first A14 fix (a per-row `EXISTS`) hit the statement timeout on the 200k demo set (500). The integration data set is too small to show it. | One lookup per query, only when a purpose is declared. Load re-run below. |

## 3. Spec gaps (Global shell, Screen 1)

| Gap | Result |
|---|---|
| G1. Map title, subtitle, active filters, time basis, freshness, "About this analysis" | **Fixed.** Visible title "Home Map — region", a subtitle (scope, period, time basis, aggregation), the active filters, the `DEMO / SYNTHETIC DATA` label from the response, data freshness, and the section renamed. Time-basis and confidence limitations are added to About. |
| G2. Search by category and date range | **Fixed:** category matches (sensitive ones only with access) and `YYYY-MM` / `YYYY-MM-DD` periods. Landmark and road segment remain "not available": the demo data model has none. |
| G5. Evidence/source reference required | **Fixed** (form and API body). |
| G6. Review Queue badge count, notifications, accessibility/help menu | **Partly fixed.** A badge from the new `GET /review-queue/summary`, counting only what the caller may decide (tested), and a help/accessibility menu. Notifications is an honest stub that says there is no notification source until the Phase 6 workflows. |
| W1. Keyboard access to map features (WCAG 2.1.1) | **Fixed:** a "Features in view" list (largest 50) opens the same drawer. Verified with keyboard focus and Escape in the browser. |
| G3. Civic-context layer follows the flag | **Accepted:** the flag is off by default and there is no civic data. The layer and the record kind are shown disabled with the reason. |
| G4. Beat notes; Open Source Record | **Partly.** Records are now clickable. Beat notes are not supported by the notes model (a message says so). Open Source Record stays a labelled stub: no source system is connected. |
| G7. Legend: pattern tag; grid scale | **Partly:** the grid legend states the scale (lighter 1, darkest 40+). The pattern tag stays a Phase 5 placeholder. |

## 4. Accepted, residual or blocked

| Item | Status |
|---|---|
| D4. `app.scope_global` is a session flag that SQL running as the API role could set itself, the same way it could set `app.region_ids`. All row-level-security session settings are asserted by the API (ADR-002 trusts the API's session). Settings are transaction-local, so there is no leak between pooled requests. Aggregate-only global roles (DEMO_USER, SENIOR_LEADERSHIP) get row access with the API applying aggregation. A SECURITY DEFINER aggregate layer would make RLS independent for them. | **Accepted, documented**; candidate for Phase 8 hardening. |
| A13. Intersecting non-nested hexagon sizes, or overlapping queries, can narrow where a small group lies (no secondary suppression across queries). | **Accepted residual risk** (ADR-012). |
| 422 validation failures are not audited (no access decision and no data). | **Accepted, documented** in api.md. |
| Personal names typed into search cannot be detected for redaction. Search never matches names. | **Accepted, documented.** |
| `visibility_classification` on records and notes is a label. Every role's ceiling includes RESTRICTED, so no policy uses it. | **By design** (data-classification §1). |
| D7. With the occurrence time basis, records without an occurrence time drop out. With a confidence floor, records without a location drop out. | **Documented** in the response's About section. |
| D8 and D9. A region CRS change is not propagated to stored metres (CRS is fixed by design). `core.hex_cell` duplicates the inline maths. Some indexes overlap. | **Low, open.** |
| Default dates are fixed when the page loads (a tab left open past midnight keeps yesterday until Reset). Search is not a full ARIA combobox (no arrow keys; Tab works). | **Low, open.** |
| S4. In points mode, categories differ by colour only on the canvas. The legend, drawer and feature list name them, so the information is not colour-only overall. | **Low, open** (a shape or pattern per category). |
| Hexagon queries miss the ADR-017 target at 10 concurrent users (below). | **Open** (Phase 3 report §5). |
| `braces` ≤ 3.0.3 high advisory (GHSA-vfj7-8cjw-p6xm) via `eslint-config-next`, dev only. No patched release exists on the registry. The production audit gate (`pnpm audit --prod`) passes. | **Blocked** (upstream). |
| Accessible names of label-wrapped inputs, and WCAG conformance overall: no axe or screen-reader test was run. The browser tool's accessibility tree showed some wrapped inputs without names; whether real assistive technology announces them is unverified. | **Blocked / unverified** (Playwright and axe not set up). |
| Region switching in the browser: the only test account available to me (`demo.steward`) has one region. | **Unverified in the browser** (code and TanStack behaviour only). |

## 5. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit, integration (testcontainers PostGIS, with migrations 0001–0006 and the round trip) and worker tests | **441 passed**, 1 deprecation warning from before Phase 3 (was 423; +17 audit regression tests, +1 badge test) |
| Earlier intermittent failure (`test_grant_for_unknown_user_is_404`) | Not reproduced in 21 further full runs. Cause unknown. |
| Mutation checks | 17 of 18 audit-fix mutations are caught. The remaining one is covered by a second database rule (see the D2 note). |
| ruff, ruff format, mypy --strict (API 41 files, worker 9, as CI runs it) | clean |
| Web: ESLint, tsc, Vitest (**87**, was 75), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` (bandit, pip-audit, pnpm audit --prod, gitleaks, semgrep, trivy) | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose database migrated to 0006; API, worker and scheduler rebuilt | done. Two transient image-build failures (PyPI timeout, DNS) needed retries. |
| Browser, as `demo.steward`, 1440 × 900 | Map, header and badge render; no CSP violations with the dialog open; picking, Escape and typed coordinates work; the API's reason is shown; the feature list, drawer isolation, Escape and focus return work; the date search applies the period; the help menu works; **39 resources, all same-origin** (HM-6). |

Load re-run after the fixes (in-process harness `scripts/bench_map.py`, same 2-vCPU VM, p95):

| Query | 1 user | 10 concurrent |
|---|---|---|
| Hexagons 500 m, PDY, 24 months | 389 ms | 3,299 ms |
| Hexagons 250 m, PDY, 90 days | 189 ms | 2,037 ms |
| Beats, PDY, 12 months, theft | 68 ms | 525 ms |
| Points, one station, 90 days (falls back to hexagons) | 93 ms | 574 ms |
| Points, PDY, 30 days, view box (new) | 72 ms | 682 ms |
