# Phase 3 Delivery Report — Home Map

**Date:** 2026-10-12 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §8.

> **Audited 2026-10-12** — see [phase-3-audit.md](phase-3-audit.md). The audit fixed a critical
> privacy defect (a view box could locate the exact point of a snapped record), high-severity
> leaks of small sensitive counts and of sensitive incidents through operational records, a
> scope-copy race, and database-level gaps in review enforcement (migration 0006). It also fixed
> 20 correctness and usability defects and closed most Home Map spec gaps. Figures below are from
> the original Phase 3 run; current totals are in the audit (441 Python, 87 web tests).

Nothing has been committed or pushed: the repository has no commits and no remote, so CI has not run
on GitHub. Every CI step was run locally (§4). CRIMEMAP is **not** claimed to be production ready.

## 1. Specification acceptance criteria

| Criterion (spec Phase 3) | Result | Evidence |
|---|---|---|
| A territory-scoped user cannot query or view another territory's restricted records | **Met (API); checked by hand in UI** | `test_home_map.py`:<br>• A station officer naming another station, beat or region gets a 403 that is audited (AUTHZ_DENIED). Their summary or details for another territory's record give a 404.<br>• Search returns nothing outside scope, and metadata lists only their own units.<br>• Row-level security is a second layer, tested with scope copies after a region move.<br>Mutation check: removing the station territory check fails the tests. |
| Map filters update counts, legend and status bar accurately | **Met** | One SQL definition of the selection (`core.map_selection`) feeds features, legend and summary (ADR-032). Tests assert, for POINTS, HEX and BEAT, that the feature counts and legend total equal `summary.shown`. They also assert that `selected = shown + excluded + suppressed` and that changing a filter changes all three. Browser: changing the period or category updated the map, legend and status bar together. |
| The record popup masks restricted data by role | **Met** | Tests per role:<br>• Offence section and name only for CASE records with detail permission.<br>• Hour-level time for station officers.<br>• No coordinates in any popup.<br>• Sensitive categories 404 without `incident.sensitive_category.read`.<br>• Aggregate-only roles get no points.<br>• Residential and HIGHLY_RESTRICTED records are never points.<br>• Exact points only with exact-location scope (the steward needs `purpose=DQ_REVIEW`); everyone else gets 250 m cell centres.<br>Mutation checks: the sensitive-popup gate, the residential exclusion and the exact-location predicate each fail tests when removed. |
| A manual operational record cannot create or edit an official FIR/case record | **Met** | The dialog and API have no FIR kind (test). Route contract test (HM-5): the only write under `/incidents` is `POST /incidents/{id}/data-issues`, which creates a DQ flag. The operational record table has no write path to `incident_record`. A test confirms the incident is unchanged after records and reviews. Browser: a submitted event marker was PENDING and audited, and `incident_record` was unchanged. |

Screen tests HM-1 to HM-6 ([acceptance-test-plan.md](acceptance-test-plan.md)):
- HM-1, HM-2, HM-3 and HM-5 are integration tests.
- HM-4 is covered by a test (no FIR kind, audit event, review queue) and by the browser check.
- HM-6 was checked in the browser: all 34 resources on the Home Map came from the app's own origin.
- None of them is a Playwright test yet (§7).

## 2. What was built

| Area | Delivered |
|---|---|
| Schema (migration `0005_home_map`) | **Location storage:** metric coordinates and scope copies on `incident_location`, kept by triggers, with a backfill and `ANALYZE`.<br>**Query functions:** `core.map_selection`, `core.hex_cell`, `core.hex_polygon` and `core.scope_global()`.<br>**Row-level security:** the incident and location policies were rewritten with once-per-query scope lookups. Global case-data roles now see aggregates.<br>**New tables:** `operational_record` (review trigger, separation-of-duties CHECK, RLS) and append-only `analyst_note`.<br>**DQ flags:** an INSERT grant for user flags.<br>**Search indexes:** a `pg_trgm` locality index and a reference prefix index.<br>The downgrade is tested by the migration round trip. |
| API | **Filters and map:** `GET /filters/metadata` and `GET /map/features` (POINTS, HEX or BEAT; 3,000-point cap with fallback; suppression; exclusions; `about`).<br>**Search:** `GET /search`.<br>**Incidents:** `GET /incidents/{id}/summary`, `GET /incidents/{id}` and `POST /incidents/{id}/data-issues`.<br>**Operational records:** create, list, review and map layer.<br>**Notes:** create and list.<br>**Territory:** a shared module refuses out-of-scope units with an audited 403 and never silently narrows a request.<br>**Policy engine:** purpose-aware conditions. Details in [api.md](api.md). |
| Web | Home Map on `/`: MapLibre 6.12 canvas, filter bar, layer panel, legend, status bar, region locator, search box, and a record drawer (summary, details, notes, flag a problem).<br>"Add Operational Record" dialog with map point picking.<br>Operational-record review on `/review`.<br>Global search in the shell.<br>The map worker is served same-origin and there is no default basemap (ADR-033). Strict CSP with `worker-src 'self'`. |
| Infrastructure | Compose PostgreSQL: 768 MB, `work_mem=8MB`, `max_parallel_workers_per_gather=1`, `max_connections=60`. These came from the load test (ADR-018 note). |
| Tooling | `services/api/scripts/bench_map.py` is an in-process load harness. `apps/web/scripts/copy-map-assets.mjs` copies the map worker at build time. |
| Docs | ADR-032 and ADR-033; Phase 3 notes on ADR-006, 012, 017, 018, 019 and 020. Updates to the data dictionary, API outline, acceptance plan, traceability, README and `.env.example` (`CRIMEMAP_MAP_STYLE_URL`). |

## 3. Defects found and fixed during Phase 3

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | Hexagon query took 9.6 s (`ST_HexagonGrid` joined to points) | timing on the 200k dataset | Arithmetic binning on stored metric coordinates (ADR-032) |
| 2 | A wrapped SQL function was not inlined, so planning cost rose from 138 to 939 ms | `EXPLAIN ANALYZE` | Arithmetic written inline in the query |
| 3 | The location RLS policy looked up the incident for every row (858k buffer hits) | `EXPLAIN (BUFFERS)` | Scope columns copied onto the location row, kept by triggers |
| 4 | RLS scope lookups were evaluated for every row | `EXPLAIN` | Scalar subqueries (`CAST((SELECT …) AS uuid[])`) evaluated once per query |
| 5 | Global aggregate roles (demo user, senior leadership) saw empty maps: RLS had no notion of "global" | integration test | `app.scope_global` flag set only for global case-data grants |
| 6 | PostgreSQL was OOM-killed under 10 concurrent map queries, and the API returned 500s | load test | Memory and parallelism limits in Compose |
| 7 | Bad query plans after the migration backfill (stale statistics, table bloat) | timing after migrate | `ANALYZE` in the migration; README note for bulk loads |
| 8 | Duplicate OpenAPI schema names across modules broke the generated web types | type generation | Classes renamed; a guard test checks that names are unique |
| 9 | A tiny sensitive selection (1–2 records) could be recovered by subtracting counts | review of the suppression logic | Totals of 1–2 suppressed records are dropped from all counts and labelled "fewer than 3" (mutation-checked) |
| 10 | A steward with approve permission could have decided any operational-record kind | review against the access matrix | Stewards may decide location corrections only, and their queue shows only those (test) |
| 11 | The Home Map was blank in a hidden browser tab | browser check | Not a defect: browsers pause rendering in hidden tabs. Documented in ADR-033. |

## 4. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit + integration + worker tests (testcontainers PostGIS) | **423 passed** (1 Starlette/httpx deprecation warning from before Phase 3). Phase 3 added `test_home_map.py` (~35 tests) and unit tests for routes, policy and OpenAPI names. |
| Intermittent failure | In one of 11 full or integration runs, `test_api.py::test_grant_for_unknown_user_is_404` (a Phase 1 test) failed. The status code it got was not captured. It passed in isolation and in all 10 later runs. No order dependence was found in the route, rate limiter or token. **Cause unknown; recorded, not fixed.** |
| Mutation checks | 6 of 6 caught (residential exclusion, exact-location predicate, station territory check, sensitive popup gate, review separation of duties, selection-level tiny suppression); all restored |
| ruff, ruff format, mypy --strict (API 40 files, worker 9 files) | clean. The worker check, run the way CI runs it, was **failing**: its config lacked the pydantic plugin, so `WorkerSettings()` looked like it was missing arguments. Fixed in `services/worker/pyproject.toml`. |
| Web: ESLint, tsc, Vitest (75), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` (bandit, pip-audit, pnpm audit, gitleaks, semgrep, trivy) | all PASS. Trivy first failed with a vulnerability-database download timeout (network, not a finding) and passed on retry. |
| `scripts/check_docs.py` | no problems |

Browser check, signed in as `demo.steward` in the Claude desktop app's built-in browser at 1440 px, with the
production build on port 3100. Results:
- The map rendered the region and station outlines and the hexagons.
- The steward's points snapped to 250 m. Above 3,000 points the map fell back to hexagons and
  stated the reason.
- The drawer showed only the permitted fields. Details loaded, and a note was added. A note containing
  `<b>` was rendered as text.
- An event marker with a picked point was saved as PENDING with station PDY-S01 and beat PDY-S01-B1.
  It was audited, and `incident_record` was unchanged.
- Search for a station applied the station filter.
- All 34 resources came from the app's own origin (HM-6).

Live Compose database (199,773 incidents, migration 0005):
- Every location has its scope copies and metric coordinates.
- The audit chain verifies.
- Re-ingesting the demo batches is still a no-op.

## 5. Performance (ADR-017)

Measured with `scripts/bench_map.py`: the real API code in-process against the Compose database as
`crimemap_app`, with row-level security on. Machine: a laptop with a Colima VM (2 vCPU, 4 GB) and
PostgreSQL limited to 768 MB. This is **not** k6, and the load generator shares the API's event loop.

| Query | 1 user p95 | 10 concurrent p95 | Target p95 < 500 ms at 10 users |
|---|---|---|---|
| Hexagons 500 m, Puducherry, 24 months | ~0.5 s | ~3.4 s | **not met** |
| Hexagons 250 m, Puducherry, 90 days | 0.19 s | ~1.7 s | **not met** |
| Beats, Puducherry, 12 months, one category | 0.07 s | ~0.48 s | met |
| Points, one station, 90 days | 0.10 s | ~0.55 s | marginal (not met) |

The cause is CPU: two vCPUs are shared by ten whole-region scans. Options are listed in ADR-017:
pre-aggregated grids, vector tiles, a response cache keyed by scope, or more CPU. None has been
taken yet. The target remains open.

## 6. Security and privacy notes

- Deny by default. Every map, search, popup and record call checks the permission and its territory
  in the API, and PostgreSQL row-level security applies again underneath.
- Refusals are audited. Out-of-scope records return 404 rather than revealing that they exist.
- No individual risk scores, suspect prediction, facial recognition or automated enforcement. The
  map shows records and aggregates only.
- Personal identifiers typed into search are redacted in the audit log.
- Notes and records are plain text and rendered escaped (checked with HTML input).
- With no basemap configured, the browser makes no third-party requests. The CSP allows only the
  configured style origin.

## 7. Known limitations and open items

| Item | Status |
|---|---|
| Hexagon queries miss the ADR-017 concurrency target (§5) | open |
| No Playwright e2e, axe or visual-regression tests; HM-6 and the UI checks were done by hand | open (planned for Phase 8; could start earlier) |
| `demo.analyst` cannot be used in the browser: its TOTP was enrolled during Phase 1 with a key that was not kept | workaround: reset that user's OTP in Keycloak; the browser check used `demo.steward` |
| The map does not zoom to a station after a filter or search | open (minor) |
| At 1024 px with the drawer open the layout is cramped | open (minor) |
| Some label-wrapped inputs need explicit accessible names | open (minor) |
| An approved location correction is not yet applied to the analytical location | by design until Phase 6 (review workflow) |
| ACTION_COMPLETION records (Phase 6) and pattern-review creation from the map (Phase 5) | refused / deferred |
| Landmark and road-segment search | no data source; the API reports `not_available` |
| Tamil message catalogue | not yet added |
| Finding F1 from the Phase 2 audit (needs an owner's decision) | still open, see [phase-2-audit.md](phase-2-audit.md) |

## 8. Go / no-go

**Go for Phase 4 (PY Dashboard) in DEMO**, on these grounds:
- All four Phase 3 acceptance criteria are met at the API level.
- The UI was checked by hand.
- Tests, linters, types, the build and security scans are clean.

Two items should be decided before or during Phase 4:
1. Whether the hexagon performance gap needs fixing now (pre-aggregation), or whether the
   evaluation hardware will have more CPU.
2. Whether to set up Playwright now, so that HM-1 to HM-6 and the coming PD tests run automatically.
