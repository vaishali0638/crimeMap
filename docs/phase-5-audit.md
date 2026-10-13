# Phase 5 Audit — Analytics Workbench and Controlled Exports

**Date:** 2026-10-12 · **Scope:** every Phase 5 source file and configuration: migration 0008,
`analytics/` (query, SQL, sections), the analytics, reports and notes routes, the export renderers
(CSV, PDF), the worker purge job, the workbench and Reports pages, the BFF change, and tests and
docs. Checked against `crimemap_master_build_prompt.md` (Screen 3, Reports, Analytics engine,
Pattern review creation, Phase 5) and ADR-012/014/035. Environment: DEMO, synthetic data only. Not a
production-readiness claim.

Method:
- Two independent reviews (backend and web), with backend findings proved by throwaway tests.
- Each finding re-checked by hand; fixes come with regression tests.
- The key fixes mutation-checked: reverting the fix makes its test fail.
- A browser pass as `demo.supervisor`.

| | Rows below |
|---|---|
| Fixed | 36 |
| Partly fixed | 4 |
| Accepted / documented | 3 |
| Open | 6 |
| Blocked | 2 |

## 1. Privacy and access (fixed)

| # | Severity | Finding (proved) | Fix | Test (mutation-checked ✓) |
|---|---|---|---|---|
| B1 | **High** | Totals minus bucket sums revealed small sensitive groups. The ≥3 rule was applied to the period total but separately per bucket, so `record_count − Σ weeks/cells/hours` exposed groups of 1–2. Proof: 38 − 36 = 2. | For callers without sensitive access over the whole region, sensitive records are in **no** time or place bucket, only in the combined total (≥3) and the category list. "Combined" is decided by region-wide grants, not the data. | `test_totals_minus_buckets_reveal_nothing_but_the_combined_figure` ✓, `test_daily_buckets_…` |
| B2 | **High** | A data steward (Data Quality only, no sensitive access) could read an analyst's Overview snapshot, including sensitive categories. Notes and pattern reviews could cite it the same way. | Snapshots record whether sensitive categories were combined. Other readers need the section's permission (steward: DQ only; `analytics.compare` for comparisons) and, for uncombined snapshots, sensitive access over the coverage. | `test_snapshots_are_readable_only_with_the_access_that_produced_them` ✓ |
| B3 | **High** | A comparison snapshot leaked another territory: a PDY-only analyst could read a PDY-vs-KKL snapshot. | Snapshots record every unit covered (migration 0009 `region_ids`, `station_ids`). A reader must be allowed all of them. | same ✓ |
| B4 | Medium | An SHO's region-wide snapshot returned 500 (row-level security insert check on a NULL station). | Coverage holds the SHO's stations, and the policy admits station overlap. | `test_station_scoped_snapshots_and_exports_work` |
| B5 | Medium | An SHO could never export, although the role matrix grants station-scope export. | Exports are authorised over the result's coverage (each station, or the regions). Purpose and fresh sign-in are unchanged. | same ✓ (also guards roles with analytics but no export) |
| B6 | Medium | Repeated cell keys inflated a pattern review's record and baseline counts (one cell counted 3×). | Cells must be distinct (422). | `test_pattern_review_cells_must_be_distinct…` ✓ |
| B8 | Medium | The pattern review list relied on row-level security, which uses the union of all grants. | Filtered to the `pattern_review.create` territory. | full suite |
| B10 | Low | Station-based sensitive access left records with no station masked but counted without the ≥3 rule. | Covered by B1. The dashboard's row rule now also applies whenever masked rows exist. | dashboard suite |
| B13 | Low | Notes on snapshots used the weaker rule. | Same rule as reading the snapshot. | B2 test |

## 2. Correctness, audit and robustness (fixed)

| # | Finding | Fix |
|---|---|---|
| B7 | CSV turned negative numbers and the "-" placeholder into text, so differences became non-numeric | Numbers stay numbers; missing values are empty cells. Formula neutralisation is kept (unit tests). |
| B9 | "Open review items" counted pattern reviews region-wide, ignoring the station and period filters | Filtered by station and period overlap |
| B11 | `GET /reports` was not audited. `EXPORT_REQUEST` was lost if generation failed. Over 5 MB surfaced as a DB error. | List audited. The request is written in its own transaction. 413 with a clear message. |
| B12a | Year-over-year on 29 February shifted both ends by 365 days | Each end moved separately (29 Feb → 28 Feb). Unit test. |
| B12b | `compare=REGION&a=PDY&b=PDY` was accepted | 422 "A and B must differ" (test) |
| M1 | `report_export.section` had no CHECK | Added (migration 0009) |
| W1 | Case-trend, status and A/B lines were the same colour (WCAG 1.4.1) | Colour-blind-safe palette plus a distinct dash and marker per series |
| W2 | Maps re-fitted and re-uploaded data on every render (new objects each render) | Stable memoised inputs |
| W3 | Maps had no region outline or legend; the density scale was unexplained | Region outline, legend in words for counts and density, region-line key |
| W4 | Note, Evidence and Pattern Review could be submitted twice; errors were not announced | Busy and saved states; errors use `role="alert"` |
| W5 | Export dialog state survived reopening and section changes (an old "Ready" link) | Keyed by analysis, reset on open; expiry shown in IST |
| W6 | Re-sign-in for exports lost the analysis | Section and applied filters are kept in the URL and restored after validation, value by value (unit test). Verified in the browser after sign-in. |
| W7 | Actions could run on new parameters while old numbers were shown; pattern-review cells could come from stale data | Actions disabled, with the reason, while a newer result loads |
| W8 | `?section=comparison` bypassed the sidebar's permission check | Checked in the page and the workbench |
| W9 | Errors were generic or wrong (CSRF, 401 without a sign-in link, 413/414, 503) | Specific messages; sign-in link on 401 |
| W10 | Partial ARIA tabs; no announcement on section change | Full tabs pattern (roving focus, arrow keys, Home/End, `aria-controls`/`labelledby`); a polite live region announces the loaded analysis. Verified in the browser. |
| W11 | Chart accessible name | ECharts' ARIA description with the chart title; figure caption with title and subtitle |
| W12 | Hard-coded English validation messages, production badge glitch, raw filter keys | Messages in the catalogue; badge fixed; filter summary in words |
| W13 | Every revisit refetched and re-audited; metadata URL not encoded | 60 s reuse; URL encoded |
| W14 | Reports page showed errors as "no exports"; `String(null)` dates | Error message; null-safe dates |
| W15 | Disabled actions gave no reason | Reason shown under the button |

## 3. Spec gaps (Screen 3)

| Gap | Result |
|---|---|
| Per-chart subtitle with count, time basis, period and freshness | **Fixed** (verified in the browser) |
| Overview open review list; link to the Review Queue | **Fixed** (verified) |
| Data Quality feed health and a link to the DQ Review Queue | **Fixed** (8 feeds; verified) |
| Case Trends daily/weekly/monthly control | **Fixed** |
| Hotspot persistence visual | **Fixed** (chart; the table already existed) |
| Time range across midnight | **Fixed** (explained; the API already supported it) |
| Restricted point map for authorised roles | **Partly:** a link to the Home Map points view; no points layer in the workbench |
| Overview "small map" | **Partly:** the map is a view of the Overview, not a thumbnail next to the KPIs |
| Area / Grid filter | **Partly:** beat and locality filters exist; no grid-cell filter |
| Raw codes in some labels (record types) | **Partly** |

## 4. Accepted, open or blocked

| Item | Status |
|---|---|
| Differencing across separate queries (station vs region, two hour windows) can narrow numbers; no secondary suppression across queries | **Accepted residual** (ADR-012, ADR-035) |
| Unknown or out-of-region comparison codes return 403 rather than 422 | **Accepted** (no existence oracle) |
| PDF renders non-Latin text (e.g. Tamil localities) as "?" | **Accepted, documented** (dependency-free writer) |
| Partial first and last week/month buckets in Case Trends are not marked (the dashboard marks them) | **Open, low** |
| Overview trend ignores the `grain` parameter | **Open, low** |
| Snapshots have no retention, and each stores full GeoJSON | **Open** (retention policy needed before production) |
| Each section re-runs the selection 4–9 times; concurrency target missed (Phase 5 report §5) | **Open** |
| Metadata fetched twice on first load | **Open, low** |
| Visual check of the new region outline and legend on analytics maps: the browser pane was hidden (`document.hidden`), so maps do not draw | **Blocked** (the code path is shared with the Home Map, which renders outlines) |
| Playwright, axe, screen-reader tests | **Blocked / not set up** |
| Carried over: Phase 4 audit unfinished (12 web findings, no backend audit) | **Open** |

## 5. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0009 and the round trip) and worker tests | **489 passed** (was 483; +6 audit regression tests incl. 2 unit), 1 deprecation warning from before Phase 3 |
| Mutation checks (audit fixes) | 4 of 4 caught: bucket rule, snapshot access, export coverage, distinct cells |
| ruff, ruff format, mypy --strict (API 51 files, worker 10) | clean |
| Web: ESLint, tsc, Vitest (**98**), `next build` | clean |
| OpenAPI regenerated vs committed copy | in sync |
| `scripts/run_security_checks.sh` | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose | Migrated to 0009; API, worker and scheduler rebuilt |
| Browser (`demo.supervisor`) | After sign-in the URL restored Case Trends with its filters. Chart subtitles show count, basis, period and refresh. Arrow keys move between view tabs; the panel is linked; the live region announced "Case Trends: 4,182 records". Overview open reviews and the Review Queue link, and Data Quality feed health (8) were shown. 0 CSP violations, 0 runtime errors. The analytics map outline could not be seen (pane hidden; see §4). |
