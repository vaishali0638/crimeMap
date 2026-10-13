# Phase 4 Delivery Report — PY Dashboard

**Date:** 2026-10-12 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §7.

Nothing has been committed or pushed: the repository has no commits and no remote, so CI has not run
on GitHub. Every CI step was run locally (§4). CRIMEMAP is **not** claimed to be production ready.

## 1. Specification acceptance criteria

| Criterion (spec Phase 4) | Result | Evidence |
|---|---|---|
| Region filtering updates the station list, map and trends consistently | **Met** | One filter state drives all three; the trends request carries the same region and station (`dashboard-query.test.ts`). API (`test_region_filter_is_consistent_across_boards`): for one region, the region, time and station boards agree, and every listed station has a row, including zero rows. Records without a station are counted in the region and time boards only, and the station board says so. Browser: choosing Puducherry updated the list, map and boards. |
| Station selection highlights the correct geometry and tags | **Met (checked by hand)** | Browser, as `demo.steward`:<br>• selecting Station 03 fitted the map to its synthetic boundary and drew the boundary prominently;<br>• that station's cells were drawn over the dimmed region cells;<br>• the sidebar row showed ▶ and kept the selection;<br>• the URL held `?station=PDY-S03`.<br>The highlighted cells are the API's map features with `station=PDY-S03`; the station's selected count matches its board row. Not a Playwright test yet. |
| Trend boards show filters, definitions, period, quality caveat and export/logging restrictions | **Met** | Every board returns and shows: period, category, time basis, scope, measure definition, comparison definition, data-quality caveat (share with a usable location, share with a DQ warning), last refresh, `DEMO / SYNTHETIC DATA`, the audit/export notice, and "About this analysis" limitations. `test_every_board_shows_its_context` (7 boards); checked in the browser. |
| Rate board fails safely with "rate unavailable" if the denominator is missing | **Met** | No approved denominator exists in the demo. The board says "Rate unavailable — no approved denominator. Ranked by record count instead.", every row is labelled, and ranking is by count. An unapproved (PROPOSED) denominator does not count (mutation-checked). A labelled test-only approved denominator exercises the rate path (`test_rate_board_uses_an_approved_denominator_when_one_exists`). |

Screen tests ([acceptance-test-plan.md](acceptance-test-plan.md)):
- PD-1, PD-3, PD-4, PD-5 and PD-6 are integration tests; PD-6 is also a web catalogue test.
- PD-2 and the UI side of PD-1 and PD-3 were checked in the browser.

## 2. What was built

| Area | Delivered |
|---|---|
| Schema (migration `0007_dashboard`) | `core.map_selection` also returns `event_at` (time on the chosen basis) and `locality`. Same arguments, so every map query is unchanged. The downgrade is covered by the migration round-trip test. |
| API (`routes/dashboard.py`) | `GET /dashboard/stations`: regions, stations and filterable categories in the caller's aggregate scope.<br>`GET /dashboard/stations/{code}`: station, region, synthetic jurisdiction (beats, area, boundary version), selected count, case status (active, detected, closed) with summary access only, category counts, combined sensitive count.<br>`GET /dashboard/trends/{board}` for `top-spot` (500 m cells named by their most common locality), `station-rate`, `region`, `location`, `time` (IST hour), `month`, `week`: rank, label, count, previous, change and movement, rate or "rate unavailable", partial buckets, View on Map target. ADR-034. |
| Web (`/dashboard`) | Header `PY Dashboard` and `← Back to Home`; a sidebar `← Back`.<br>Region filter (All Regions and the regions in scope), station search, scrollable station list with `View Details`.<br>Category, period and time-basis filters; `TRENDS`.<br>Map with region and station boundaries, the selected station emphasised and fitted, its tags highlighted over the dimmed region; legend and status bar.<br>View Details drawer with `View Station on Map` and `Back`.<br>Trends dialog: seven ARIA tabs (arrow keys), a context header, a ranked table with movement shown as a symbol and in words, `View on Map` (region, station or cell).<br>Region and station are kept in the URL. |
| Shared map component | `MapCanvas` gained optional highlight, selected-station and fit-to props; the Home Map is unchanged. |
| Tooling | `scripts/bench_map.py` now also times the dashboard endpoints. |
| Docs | ADR-034; API outline, data dictionary, acceptance plan, traceability (R-SCR-2, R-SAFE-5), README. |

## 3. Defects found and fixed during Phase 4

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | The region board was built from station rows, so records without a station (no usable location; 7,015 in the demo set) were missing while the time and month boards counted them. | `test_region_filter_is_consistent_across_boards` and `test_month_and_week_boards_…` | Region totals use every record in the region. The station board states how many records have no station. |
| 2 | Stations with no records in the period were missing from the station board. | review while writing PD-1 | Every station in the selection has a row (0 when empty). Tested. |
| 3 | Two map-fit effects (region outline and station target) could override each other depending on load order. | review | A single effect that prefers the requested target. |
| 4 | The rate test passed even if unapproved denominators were used. | mutation check | The test adds a PROPOSED denominator, which must still give "rate unavailable". |
| 5 | Station details and the map show different totals for callers without sensitive access (1,409 vs 1,321 for Station 03). | browser check | Not a counting error: details combine sensitive records per station, the map per cell (both at least 3). The drawer now explains this. Accepted as part of ADR-012 residual risk (§6). |

## 4. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit, integration (testcontainers PostGIS, migrations 0001–0007 and the round trip) and worker tests | **464 passed** (was 441; +23 dashboard), 1 deprecation warning from before Phase 3 |
| Mutation checks (dashboard) | 4 of 4 caught: the per-row sensitive threshold, the sensitive-category refusal, the region territory check, approved-only denominators |
| ruff, ruff format, mypy --strict (API 42 files, worker 9, as CI runs it) | clean |
| Web: ESLint, tsc, Vitest (**93**, was 87), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` (bandit, pip-audit, pnpm audit --prod, gitleaks, semgrep, trivy) | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose database | migrated to 0007; API, worker and scheduler images rebuilt |
| Browser (`demo.steward`, 1440 × 900) | List, filters, station highlight and fit, URL, details drawer, Trends tabs with keyboard, rate-unavailable label, export notice, View on Map to a cell: all work. 0 CSP violations, no console errors, 39 resources, all same-origin. |

## 5. Performance (ADR-017)

Measured with `scripts/bench_map.py` (in-process API, Compose database, row-level security on;
Colima VM with 2 vCPU and 4 GB; PostgreSQL limited to 768 MB; caller `demo.analyst`, 15 stations in
scope). Not k6.

| Query | 1 user p95 | 10 concurrent p95 |
|---|---|---|
| Trend: Top Crime Spot, all regions, 12 months | ~1.1 s | ~5.0 s |
| Trend: Police Station Crime Rate, all regions, 12 months | ~0.3 s | ~2.1 s |
| Trend: Week, Puducherry, 12 months | ~0.14 s | ~0.96 s |
| Station details, one station, 90 days | ~0.02 s | ~0.16 s |
| (Home Map, same run) Hexagons 500 m, Puducherry, 24 months | — | ~3.6 s |

Station details meet the 500 ms target. The boards do not at 10 concurrent users on this hardware:
each reads the selected and the previous period (twice the rows), and the spot board also bins
hexagons. The options are the same as ADR-017 (pre-aggregated cells per day, a scope-keyed cache,
or more CPU); none has been taken.

## 6. Known limitations and open items

| Item | Status |
|---|---|
| Board and long-period map performance at 10 concurrent users (§5) | open |
| No Playwright, axe or visual-regression tests; PD-2 and the UI checks were done by hand | open (Phase 8; could start earlier) |
| Station-level and cell-level combining of sensitive records give different totals. Comparing them can show that some sensitive records fall in small cells (not where). | accepted residual risk (ADR-012, ADR-034), documented in the drawer |
| The dashboard map shows one region at a time; with "All Regions" it shows the first permitted region and says so | by design (ADR-007) |
| The map's first fit can be off if the window is resized right after load (MapLibre does not refit on resize) | minor |
| Points mode is not offered on the dashboard map (hexagons only, 500 m) | by choice. Points stay on the Home Map. |
| Exports from boards | not offered (Phase 5 Reports); each board says so |
| Carried over: Phase 3 audit open items, the F1 decision from the Phase 2 audit, the `demo.analyst` TOTP key, and the dev-only `braces` advisory (no patched release) | open |

## 7. Go / no-go

**Go for Phase 5 (Analytics Workbench) in DEMO.**
- All four Phase 4 acceptance criteria are met: three by integration tests and one by a hand check
  in the browser.
- Tests, linters, types, the build and the security scans are clean.

The concurrency gap (§5) applies to Phase 5 analytics as well. Decide before Phase 5 whether to add
pre-aggregation.
