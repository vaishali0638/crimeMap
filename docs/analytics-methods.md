# CRIMEMAP Analytics Methods

**Environment:** DEMO, synthetic data only.

This document states, for every number CRIMEMAP shows, what it counts, how it is computed, what its
denominator is, which time basis and filters apply, how privacy is protected, and what it cannot
tell you. Everything here is taken from the code; each section names its source as `path:function`
so a reviewer can check it (path prefixes: `api/` is `services/api/src/crimemap_api/`, `worker/` is
`services/worker/src/crimemap_worker/`, `migrations/` and bare `00nn_*.py` names are
`services/api/migrations/versions/`). Where the code does not settle a point, the text says "Not determined
from the code".

Paths are shortened: `api/` is `services/api/src/crimemap_api/`, `worker/` is
`services/worker/src/crimemap_worker/`, `migrations/` is `services/api/migrations/versions/`.

All figures are **recorded counts**: records that reached CRIMEMAP and that the caller is authorised
to see. They reflect reporting behaviour, patrol presence, recording practice and data quality as
well as what happened. They are not a measure of how much happened in a place, and no number here
is a judgement of a place, station or locality.

## 1. Common foundations

### 1.1 One selection

Every map, dashboard and workbench statement reads one SQL function, so totals on different screens
cannot drift (ADR-032, ADR-034, ADR-035).

- `core.map_selection(...)` returns the incident records of **one region** that match the filters.
  It is `SECURITY INVOKER`, so row-level security applies inside it (HIGHLY_RESTRICTED records are
  invisible to the application).
- `core.analytics_selection(...)` is the same selection plus the workbench-only filters (locality
  text, IST time of day) and the ADR-012 `masked` flag.

| Filter | Rule in the selection |
|---|---|
| Region | Always exactly one region (`i.region_id = p_region`). "All regions" on the dashboard runs one selection per region the caller may see. |
| Territory | The record's region or station must be inside the scope of the permission in use (ADR-028). Row-level security is a second, independent filter. |
| Station, beat | Optional lists; the record's assigned station or beat must be in the list. |
| Category | Optional list of analytical categories. |
| Record type | Optional list (`CASE`, `ERSS_CALL`, `ACCIDENT`, `TRAFFIC_EVENT`). NULL means all record types. |
| Status | Optional list of status codes. |
| Period | `time >= start` and `time < end`, where time is the report time when the basis is `REPORTED`, otherwise the occurrence start time. |
| Minimum location confidence | `0` (no filter), `0.4` or `0.8`. When above 0, only records whose location confidence is at least that value; records with no location row have no confidence and so drop out. |
| Locality (workbench) | Case-insensitive substring match (`ILIKE`) on the recorded locality, with `%`, `_` and `\` escaped. |
| Time of day (workbench) | IST hour of the record's time on the chosen basis, from `hour_from` to `hour_to` inclusive; a window that wraps midnight (for example 22 to 04) is supported. |

Source: `migrations/0005_home_map.py`, `0007_dashboard.py`, `0008_analytics.py` (`core.map_selection`,
`core.analytics_selection`); `api/analytics/query.py:Resolved.args`.

### 1.2 Time basis and IST day boundaries

- **Occurrence time** (default) is `occurrence_start_at`. Records without an occurrence time are
  not in any occurrence-time result; every response says so and suggests report time.
- **Report time** is `reported_at`.
- A period is a range of whole IST calendar days. `period_from` starts at 00:00 IST on that day; the
  period ends before 00:00 IST on the day after `period_to` (end exclusive).
- Default period: the 90 days ending today (IST). Maximum length: 731 days. Periods must lie between
  2000-01-01 and one year from today; otherwise a 422 (`query-too-broad` for length).
- Hours, days of week, weeks and months are all taken in Asia/Kolkata. Weeks start on Monday
  (`date_trunc('week')`, ISO).

Source: `api/routes/map.py:_period`; `api/analytics/query.py:_window`.

### 1.3 Usable location

A record has a **usable location** when its location geometry exists **and** it lies inside a
synthetic beat (`geometry IS NOT NULL AND beat_id IS NOT NULL`). Records without geometry count as
"no location"; records with geometry but no beat count as "outside every synthetic beat"
("outside jurisdiction"). Only usable locations are placed in cells, beats or points.

### 1.4 Analysis CRS and the hexagon grid

Each location stores its point in the region's UTM zone, in metres (`analysis_x`, `analysis_y`,
`analysis_crs`): EPSG:32643 for Mahe, EPSG:32644 for Puducherry, Karaikal and Yanam (ADR-005). A
trigger keeps these columns in step with the WGS 84 geometry. Grids are never built across regions.

Hexagons are **pointy-top** cells in **axial coordinates** (q, r). The size `s` is the
**circumradius** (centre to corner) in metres. For a point (x, y):

1. Fractional axial coordinates: `q = (√3/3 · x − y/3) / s`, `r = (2/3 · y) / s`.
2. Cube rounding: with the third coordinate `−q − r`, round all three, and recompute the one with
   the largest rounding error from the other two.

The cell polygon is drawn from its centre `cx = s · √3 · (q + r/2)`, `cy = 1.5 · s · r`, with
corners at angles 30° + 60°·k, then transformed to WGS 84 for display. Only cells that contain
records are drawn (`core.hex_polygon`). A cell key is `REGION:size:q:r`.

| Size `s` (circumradius) | Where offered | Cell area (derived, `3√3/2 · s²`) |
|---|---|---|
| 250 m | Home Map, Location Trends, KDE grid | about 0.16 km² |
| 500 m | Home Map (default), Location Trends (default), Top Crime Spot board | about 0.65 km² |
| 1000 m | Home Map, Location Trends, Overview map, Comparison maps, emerging-pattern detector | about 2.6 km² |
| 2000 m | Home Map, Location Trends | about 10.4 km² |

Source: `migrations/0005_home_map.py` (`core.hex_cell`, `core.hex_polygon`); the same arithmetic is
inlined in `api/routes/map.py:_HEX`, `api/routes/dashboard.py:_SPOTS`, `api/analytics/sql.py:CELLS`.

### 1.5 Privacy rules that apply everywhere

| Protection | Rule |
|---|---|
| Authorised records only | Records outside the caller's territory, and HIGHLY_RESTRICTED records, are not counted anywhere. The response does not say how many were left out. |
| Residential generalisation | A residential location is stored in the analytical store only as a point snapped to a grid of 0.0025° (about 250 m) and moved into its assigned beat if snapping left it (`core.generalise_point`); precision becomes `GRID`. The exact point exists only in the restricted schema, which the API cannot read. A CHECK constraint forbids an exact residential point in core (ADR-031). |
| Residential never as points | Residential records are never returned as points. They are counted in hexagons, beats and all aggregates at their generalised position. |
| Combined sensitive categories (ADR-012) | For a caller without `incident.sensitive_category.read` covering the selection, sensitive categories are "masked": they are combined into one "sensitive" count. A combined count is shown only when it is **at least 3** (`SMALL_CELL = 3`, a constant in `api/routes/map.py`, not a setting). Smaller groups are **counted nowhere**, and the response gives the same sentence ("sensitive records are counted only in groups of 3 or more") whether 0, 1 or 2 were left out. |
| No single sensitive category | Such callers cannot filter on a sensitive category (audited 403), and the category is not offered as a filter. |
| Decided from grants, not data | Whether masking applies is decided from the caller's grants and the request, never from the data, so the shape of a response cannot reveal whether sensitive records exist. |
| Point precision | Exact coordinates only within the scope of `incident.location.exact` (ADR-020). Otherwise a point is the centre of its 250 m square cell in the region's UTM zone (`floor(x/250)·250 + 125`, the same for y). |

Accepted residual risk (ADR-012, ADR-035): comparing overlapping queries (different hexagon sizes,
one station against the region, two hour windows) can narrow where a small sensitive group lies.
Secondary suppression across queries is not attempted.

Where the ADR-012 group is applied differs by screen; see §2.4, §3.5 and §4.6.

## 2. Home Map

`GET /api/v1/map/features` answers one question for one region: which records match these filters,
and how may this caller see them? It returns GeoJSON plus a status-bar summary, legend, the
aggregation applied, the period and an "about" block.

Source: `api/routes/map.py:map_features`.

### 2.1 Aggregations

| Aggregation | Permission | What a feature is | Count in a feature |
|---|---|---|---|
| `POINTS` | `map.points.read` | One record with a usable location, not residential, not masked | 1 |
| `HEX` (default) | `map.aggregate.read` | One hexagon of 250, 500 (default), 1000 or 2000 m circumradius | Records with a usable location in the cell, by category, plus the combined sensitive count if at least 3 in that cell |
| `BEAT` | `map.aggregate.read` | One synthetic beat polygon (current boundary version) | Records with a usable location assigned to the beat, as for hexagons |

- **Points limit:** `MAX_POINTS = 3000`. If more than 3,000 points fall in the view, the response is
  aggregated to hexagons at the requested size, with the reason "more than 3000 points in view;
  aggregated".
- **View box:** with `bbox`, points are selected by the coordinate the caller **receives** (the
  snapped one for non-exact callers), so narrowing the box cannot locate an exact point. Hexagon and
  beat answers are not limited by the view box.
- **Location precision** reported: `EXACT` (all points exact), `SNAPPED_250M` (none exact), `MIXED`,
  or `AGGREGATED` for hexagons and beats.
- **Data-quality warning:** a record whose `record_quality_status` is not `OK` (`WARNING` or
  `FAILED`) is a DQ warning. Cells and the summary report how many.
- **Unmapped:** a record with no analytical category is shown as "Unmapped offence code".

### 2.2 Exact versus snapped points

| Caller | Point returned |
|---|---|
| `incident.location.exact` without conditions in scope (CRIME_ANALYST, SHO, DISTRICT_OR_REGION_SUPERVISOR within scope) | Exact WGS 84 point |
| DATA_STEWARD with declared purpose `DQ_REVIEW` | Exact only for records with an OPEN or ASSIGNED location data-quality issue (`LOW_CONFIDENCE_LOCATION`, `MISSING_LOCATION`, `OUTSIDE_JURISDICTION`); snapped for all others |
| Anyone else with `map.points.read` | Centre of the record's 250 m cell in the region's UTM zone |

Popups contain no coordinates.

Source: `api/routes/map.py:_POINTS`, `_UNDER_REVIEW`; ADR-020.

### 2.3 Status-bar counts and "Why excluded?"

The status bar is computed from the same grouped rows as the features. Masked records are left out
of every count below; they return only as combined groups of at least 3.

| Field | Definition |
|---|---|
| `selected` | Records counted individually + combined sensitive records shown (per cell for hexagons/beats; for the whole selection for points, when at least 3) |
| `usable_locations` | Records counted individually with a usable location; for hexagons and beats, plus the combined sensitive counts drawn in cells |
| `shown` | Points drawn, or the sum of cell / beat counts |
| `dq_warnings` | Records counted individually whose quality status is not `OK` |

"Why excluded?" breakdown (`summary.excluded`, shown in the web status bar):

| Reason | Count | Why |
|---|---|---|
| No location | Records without geometry | Nothing to place |
| Outside every synthetic beat | Records with geometry but no beat | Not inside the synthetic jurisdiction |
| Residential (shown only in aggregates) | Points mode only: residential records with a usable location | Never shown as points (ADR-031) |
| Sensitive category (aggregates only) | Points mode only: the combined sensitive count, when at least 3 | Masked categories are never points |
| Outside the current view | Points mode only: eligible points (usable, not residential, not masked) minus the points inside the view box | View box |
| Not counted | No number. "sensitive records are counted only in groups of 3 or more" for callers who may be masked; "none" otherwise | ADR-012 |

In hexagon and beat modes, residential records are inside the cells (at their generalised
position), so the residential and sensitive exclusions are 0 there.

Source: `api/routes/map.py:_counts`, `_cells`, `MapSummaryOut`; `apps/web/messages/en.ts`
(`map.status.*`).

### 2.4 Privacy at cell level

In hexagons and beats, masked records are combined **per cell**; a cell's combined count is shown
only when it is at least 3, and cells whose total is 0 after this are not drawn. In points mode the
combined count is taken over the **whole selection** and reported in the legend as "Sensitive
categories (combined, not shown as points)".

### 2.5 Location confidence filter

`min_confidence` is `0`, `0.4` or `0.8` (the bands offered by `/filters/metadata`). Above 0, only
records whose location confidence is at least the value are selected; records without a location
are then not included, and the response says so.

### 2.6 Limitations

- Synthetic demo data and synthetic boundaries; not an official jurisdiction map.
- Records the caller is not authorised to see (including HIGHLY_RESTRICTED) are not counted.
- Residential locations are generalised to about 250 m.
- Snapped points are the centre of their 250 m cell, not the exact place.
- A hexagon count depends on the grid size and origin (the grid is anchored at the UTM origin); the
  same records can look more or less concentrated at a different size. This is the modifiable areal
  unit problem; no correction is applied.
- No rate or density is computed on the map; counts are not adjusted for area, population or
  exposure.
- Freshness (`last_refreshed_at`) is the latest batch time over DEMO or APPROVED sources, not per
  record.

## 3. PY Dashboard

The dashboard's station list, station details and seven trend boards read `core.map_selection`
under `map.aggregate.read` in its territory. Unlike the map and the workbench, **the dashboard does
not filter on record type or status**: every board and the station details count all record types
(cases, ERSS calls, accidents, traffic events). Minimum location confidence is always 0. Filters are
region (or "All regions" in scope), optional station, categories, period and time basis.

Source: `api/routes/dashboard.py` (module docstring; `_STATIONS`, `_SPOTS`, `_LOCATIONS`, `_HOURS`,
`_MONTHS`, `_WEEKS`, `_DETAILS`).

### 3.1 Station details

`GET /dashboard/stations/{code}`. Source: `api/routes/dashboard.py:station_details`.

| Field | Definition |
|---|---|
| `selected` | Records of the station in the period counted individually + the station's combined sensitive count if at least 3 |
| `categories` | Records counted individually, per analytical category (sensitive categories are not listed for masked callers) |
| `sensitive` | For masked callers: the combined count for the **whole station** if at least 3, otherwise the "not counted" sentence |
| `cases.active` | CASE records with status `REGISTERED` or `UNDER_INVESTIGATION` |
| `cases.detected` | CASE records with status `DETECTED` |
| `cases.closed` | CASE records with status `CLOSED` |
| `jurisdiction` | Number of beats, area in km² (geodesic area of the current boundary, 1 decimal), boundary version, approval status, source; labelled "Synthetic boundary — not an official jurisdiction" for demo boundaries |

- Case-status counts need `incident.summary.read` for the station; otherwise they are not shown and
  the note says why.
- Case-status counts exclude masked (sensitive) cases, and the note says so. Case records with any
  other status code are in no status group, so the three groups need not add up to the CASE total.
- Station details combine sensitive records for the whole station; the map combines them per cell.
  The two totals can therefore differ (ADR-034).

### 3.2 Trend boards

`GET /dashboard/trends/{board}`. Each board returns rows with `count`, `previous`, `change_pct`,
`movement`, `partial`, an optional rate and a map target. Source:
`api/routes/dashboard.py:trend_board`, `MEASURES`, `COMPARISONS`.

| Board | Unit of a row | Count in a row | Records included | Rows shown | Ranked by |
|---|---|---|---|---|---|
| Top Crime Spot (`top-spot`) | 500 m hexagon | Records in the cell | Usable location only | Top 20, cells with count 0 dropped | Count |
| Police Station Crime Rate (`station-rate`) | Station | Records assigned to the station | Records with a station | Every station in the selection, including 0 | Rate if every station has one, else count (§3.4) |
| Region (`region`) | Region | Records in the region | All selected | Every region in the selection | Count |
| Location (`location`) | Station and locality text | Records with that locality at that station | Locality recorded | Top 20, count 0 dropped | Count |
| Time (`time`) | IST hour of day (00 to 23) | Records in that hour | All selected | Hours with records | Count |
| Month (`month`) | Calendar month (IST) | Records in the month | All selected | Every month in the period, in date order | Rank by count is given, order is chronological |
| Week (`week`) | Week starting Monday (IST) | Records in the week | All selected | Every week in the period, in date order | As for month |

- Ties are broken alphabetically by row label.
- A Top Crime Spot row is labelled "Cell near *locality*", the most common recorded locality among
  the records the caller may see individually in that cell (`mode()`), as a neutral place name; if
  there is none, "Cell *key*".
- The region board equals the map's selected count for the same filters, and the month and week
  boards add up to it (tested; ADR-034).
- The station board's rows exclude records without a station; their number is stated as a
  limitation ("not assigned to a station; they are counted in the region, time, month and week
  boards only").

### 3.3 Comparison rule and movement

| Board | Previous value |
|---|---|
| Top Crime Spot, Police Station Crime Rate, Region, Location, Time | The same-length period immediately before the selected one (`start − length` to `start`) |
| Month | The calendar month before each month |
| Week | The week before each week |

- `change_pct = round((count − previous) / previous × 100, 1)`; null when `previous` is 0.
- `movement`: `UP`, `DOWN` or `SAME`; `NEW` when previous is 0 and count is above 0; `SAME` when both
  are 0.
- Month and week buckets that extend beyond the selected period are marked **partial** ("(partial)"
  in the label). A partial bucket counts only the days inside the period, so its comparison with a
  full previous bucket is not like-for-like.

### 3.4 Rates (station-rate board)

The only rate in CRIMEMAP is on the Police Station Crime Rate board (ADR-014, ADR-034).

- **Numerator:** the station row's count (records of all record types assigned to the station in
  the period, with the combined sensitive group if at least 3).
- **Denominator:** the sum of `value` over `core.denominator` rows with `measure = 'POPULATION'` and
  `approval_status = 'APPROVED'` for that station whose period overlaps the selected period
  (`period_start <= period_to` and `period_end >= period_from`).
- **Formula:** `rate = round(count / population × 100,000, 1)`, labelled "per 100,000 residents in
  the period". The rate is not annualised, and a denominator that only partly overlaps the period
  is not pro-rated. If more than one approved row overlaps, their values are summed.
- **Ranking:** by rate only when every station in the board has a rate; otherwise by count, with
  the label "Rate unavailable — no approved denominator. Ranked by record count instead." Each row
  without a denominator carries "Rate unavailable — no approved denominator".
- **In the demo there is no approved denominator**, so the board always ranks by count. A labelled,
  test-only denominator exercises the rate path. An unapproved (`PROPOSED`) denominator is never
  used (mutation-checked, Phase 4 report).
- Population, registered vehicles, road length and other exposure measures are **not available**.
  No other board, map or workbench section shows a rate; the Comparison section states "Rate
  unavailable — no approved denominator".

### 3.5 Quality caveat and privacy

- `quality.usable_location_pct = usable / selected × 100` and `quality.dq_warning_pct = dq / selected
  × 100` (1 decimal), where `selected` here is the current-period records **counted individually**
  (masked records are not in the denominator), and `usable` and `dq` are as in §1.3 and §2.1.
- The caveat says "Counts reflect what was recorded, not all that happened."
- ADR-012 applies **per board row** (and per previous-period value): a row's masked records are added
  only when there are at least 3 of them in that row.
- Every board and station-details view is audited (`ANALYTICS_QUERY`). Export is not offered on the
  dashboard.

### 3.6 Limitations

- Boards mix record types (no record-type filter), so a station's count includes calls and traffic
  events as well as cases.
- Rankings say "higher recorded count in the selected period"; they are not a judgement of a place
  or station, and a count is not adjusted for area, population or footfall.
- Locality text is as recorded at source; spelling variants are separate rows.
- Records without an occurrence time are excluded on the occurrence basis.

## 4. Analytics Workbench

Five sections share one filter model and one response envelope `{data, meta}` (api.md §2). Source:
`api/analytics/query.py`, `api/analytics/sections.py`, `api/analytics/sql.py`,
`api/routes/analytics.py`.

### 4.1 Filters and defaults

| Filter | Values and rule |
|---|---|
| Record type | Default `CASE`. `COMBINED` (all types) must be chosen explicitly and adds the caveat that calls, accidents, traffic events and cases measure different things. |
| Status | Needs `incident.summary.read` for the region (audited 403 otherwise). |
| Comparison | `PREVIOUS` (default), `YEAR` or `NONE` (§4.3). |
| Grain | `day`, `week` or `month`; Case Trends chooses one if not given (§4.5). |
| Unit | `HEX` (default), `BEAT` or `STATION` (Location Trends). |
| Hexagon size | 250, 500 (default), 1000, 2000 m. |
| Scope | `analytics.read` in its territory; `analytics.compare` for Comparison. A DATA_STEWARD's grant covers the Data Quality section only. |

Unknown parameters are refused (422).

### 4.2 The envelope (`meta`)

| Field | Meaning |
|---|---|
| `filters_applied` | Region, period, time basis, record type and every other filter used, including the comparison period |
| `record_count` | Records counted individually + the combined sensitive total if at least 3 (Comparison: both sides added) |
| `excluded_count`, `excluded_reasons` | Records left out of the map part with their reason (`no_usable_location` / `no_usable_location_on_map`); only non-zero reasons are listed |
| `suppressed` | The ADR-012 sentence for masked callers, otherwise null |
| `data_freshness` | Latest batch time over DEMO or APPROVED sources |
| `method`, `method_parameters` | The method name and its parameters (below) |
| `data_quality_note` | "*x*% of the records counted individually have a usable location; *n* carry a data-quality warning." |
| `caveats` | Synthetic data; COMBINED record types; sensitive combination; occurrence-time exclusion; section caveats |
| `classification` | `RESTRICTED` |

The Table view (`data.tables`) and every export are built from the same aggregated tables.

### 4.3 Comparison period rule

| `comparison` | Comparison window |
|---|---|
| `PREVIOUS` | The same number of days immediately before the current period: from `start − length` to `start − 1 day` |
| `YEAR` | The same calendar dates one year earlier; each end is moved separately, and 29 February becomes 28 February |
| `NONE` | No comparison |

`change_pct = round((current − comparison) / comparison × 100, 1)`; null when the comparison count is
0 or there is no comparison.

Source: `api/analytics/query.py:comparison_window`, `_year_before`; `api/analytics/sections.py:change`.

### 4.4 Sections and their measures

**Overview** (`method = counts_by_category_time_and_1000m_hexagon`)

| Measure | Definition |
|---|---|
| `kpis.selected` | `record_count` for the current period |
| `kpis.comparison_selected`, `kpis.change_pct` | The same for the comparison window, and the change (§4.3) |
| `kpis.usable_location_pct` | Records counted individually with a usable location ÷ records counted individually × 100 |
| `kpis.open_review_items` | Open (`OPEN` or `ASSIGNED`) data-quality issues on the selected, unmasked records (one per issue, not per record) + pattern reviews in the region (and stations, if filtered) with analyst status `DRAFT` or `SUBMITTED` whose period overlaps the current period. The pattern-review part is not filtered by category or other filters. |
| `trend` | Counts per week if the period is at most 200 days, otherwise per month |
| `categories` | Counts per category, plus "Sensitive categories (combined)" if at least 3 |
| `quality` | Counts per record quality status (`OK`, `WARNING`, `FAILED`) |
| `map` | 1000 m hexagons, usable locations only |
| `open_reviews` | The ten largest (issue type, severity) groups, plus draft pattern reviews |

**Location Trends** (`method = hex_grid_count`, `beat_choropleth_count` or `station_choropleth_count`)

| Measure | Definition |
|---|---|
| Hexagon counts | Records with a usable location per cell of the chosen size; each cell also carries its `comparison_count` from the comparison window. At most 3,000 cells (largest first). |
| Beat / station choropleth | Records per beat or per station (records with a station; a beat count needs a beat) |
| Persistence | Only with a comparison window. In each period, take cell counts of at least 3, sort them, and find the value at position ⌈10% of that list⌉; a cell is "top" if its count is at least that value and at least 3 (ties included). Each top cell is labelled `IN_BOTH`, `CURRENT_ONLY` or `COMPARISON_ONLY`. |
| Repeat locations | Localities (per station) with records in at least **3 distinct IST weeks** in the period; the top 25 by weeks, then count. Masked records never appear. |
| KDE (density) | Only when `record_count >= 200` **and** usable locations >= 80%; otherwise `available: false` with the reason and the actual figures. Method below. |
| Road segments | Always `available: false`: the demo data has no road association. |

KDE method (`api/analytics/sections.py:_kde`): counts are first binned to 250 m hexagons. Each
occupied cell spreads its count to cells whose centres are within 3 bandwidths (1,500 m), with a
Gaussian kernel of bandwidth `h = 500 m`: `density += n · 1 / (2π · (h in km)²) · exp(−d² / (2h²))`,
where `d = √3 · 250 · √(dq² + dq·dr + dr²)` is the centre distance. The result is records per km²
for the period. Cells below 5% of the maximum density are dropped; at most 3,000 cells are returned.
Bandwidth and thresholds are hand-set, and no edge correction is applied at region or coast edges.

**Case Trends** (`method = time_bucket_counts`)

| Measure | Definition |
|---|---|
| `series`, `by_category` | Counts per time bucket (IST) at the grain, overall and per category |
| `calendar` | Counts per IST day |
| `day_hour` | 7 × 24 grid: ISO day of week (Monday to Sunday) by IST hour |
| `delay` | Report time minus occurrence time, in bands: same day (< 1 day), 1–2 days (< 3), 3–7 days (< 8), 8–30 days (< 31), over 30 days, reported before occurrence, unknown (either time missing) |
| `status` | Only for `record_type = CASE` and with `incident.summary.read`: per IST month, active (`REGISTERED` or `UNDER_INVESTIGATION`), detected (`DETECTED`), closed (`CLOSED`). Masked cases and other status codes are not counted. |

The delay is not a response time; the section says so.

**Comparison** (`method = a_vs_b_counts`, permission `analytics.compare`)

| `compare` | Side A | Side B |
|---|---|---|
| `PERIOD` | Current period | The `PREVIOUS` window (§4.3) |
| `YEAR` | Current period | The `YEAR` window (§4.3) |
| `REGION`, `STATION`, `BEAT` | Unit `a` | Unit `b` (each in its own region; must differ) |
| `CATEGORY` | Category `a` | Category `b` (must differ) |

Each side gives `count` (`record_count` of that side), `usable_location_pct`, a series, categories
and a 1000 m hexagon map. The difference table lists, per category, `a`, `b`, `difference = a − b`
and `change_pct` relative to B, sorted by the size of the difference. Caveats are added when the
usable-location shares of A and B differ by 10 points or more, and when the periods differ in
length. Rates are never shown.

**Data Quality** (`method = completeness_and_issue_counts`)

Denominator `n` = records counted individually (masked records are not broken down here).

| Measure | Definition | Percentage of `n` |
|---|---|---|
| Missing location | Records without geometry | yes |
| Outside every synthetic beat | Records with geometry but no beat | yes |
| Approximate or low-confidence location | Records with confidence below 0.4 or no confidence, minus the records with no location | yes |
| Missing occurrence time | Records in the period **by report time** with no occurrence time (always measured on report time, whatever the chosen basis) | no |
| Possible duplicates | Open `POSSIBLE_DUPLICATE` issues on the selected records | yes |
| Invalid category/section mapping | Open `INVALID_CATEGORY_MAPPING` issues | yes |
| Late or stale source feeds | Number of source feeds whose last health status is `STALE` (not a record count) | no |

Also returned: counts per geocode method, per confidence band (high >= 0.8, medium 0.4 to < 0.8,
low < 0.4, none), per precision level, open issues by type, and feed health (last batch, health,
stale-after hours, open dead letters).

**Civic Trends** is refused (503 `feature-disabled`) while the `civic_context` flag is off (the
default), and even when it is on, because no verified civic-context source is connected.

### 4.5 Grain rule

Case Trends and Comparison use the requested grain, or, if none: `day` for periods up to 62 days,
`week` up to 200 days, `month` beyond. Overview uses `week` up to 200 days, otherwise `month`.

Source: `api/analytics/sections.py:_grain`, `overview`.

### 4.6 Privacy in the workbench

Stricter than the map (Phase 5 audit, ADR-035): for masked callers, sensitive records appear in **no
time or place bucket** (day, week, month, cell, unit, hour, day × hour, delay band, status month,
repeat locations, data-quality breakdowns). They appear only once, as the combined total in the
headline count and the category list, and only when that total is at least 3. Whether records are
masked is decided from **region-wide** sensitive grants only; a station-based sensitive grant still
masks records outside those stations.

Consequently, bucket counts may add up to less than `record_count`; the caveat says so.

## 5. Evidence snapshots

`POST /evidence-snapshots` with a section and its filters. The server **re-runs** the analysis from
the validated parameters under the caller's own access (it does not accept a result from the
client) and stores it in `core.evidence_snapshot`.

| Frozen | Content |
|---|---|
| `section` | One of overview, location, case_trends, comparison, data_quality |
| `params` | The canonical filter set (`AnalyticsQuery.as_params`) |
| `data` | The section result (without the Table view, which is derived from it) |
| `meta` | The envelope, without the internal coverage block |
| `content_sha256` | SHA-256 of the JSON of `{section, params, data, meta}` with sorted keys (non-JSON values as strings), computed before the snapshot id is set (`meta.evidence_snapshot_id` is null in the hashed form) |
| Coverage | Every region and station the result covers (both sides of a comparison; a station officer's own stations) and whether sensitive categories were combined |
| `classification`, `created_by`, `created_at` | `RESTRICTED`, the author, the time |

- The application role has only SELECT and INSERT on the table: a snapshot cannot be changed or
  deleted through the application.
- A reader other than the author needs the section's permission over everything the snapshot covers
  (Data Quality only for stewards; `analytics.compare` for comparisons), and sensitive access when
  the snapshot was computed without combining sensitive categories.
- A pattern review must cite a Location Trends **hexagon** snapshot that has a comparison period;
  its record count and baseline count are the sums of `count` and `comparison_count` over the chosen
  cells in the snapshot, so they are fixed at creation.
- A snapshot records the data **as it was** when it was made. Later corrections to records do not
  change it.

Not determined from the code: whether any endpoint or tool re-computes the hash to verify a stored
snapshot.

Source: `api/routes/analytics.py:digest`, `store_snapshot`, `create_snapshot`, `may_read_snapshot`,
`create_pattern_review`; `migrations/0008_analytics.py`.

## 6. Outcome review counts

An outcome review follows a COMPLETED operational action. The server computes the counts; the
reviewer chooses the periods and an optional comparison area (ADR-036).

Source: `api/analytics/outcome.py:measure`, `ring`, `change_summary`;
`api/routes/workflow.py:create_outcome`, `OutcomeIn`.

### 6.1 Definition

- **Which records:** the pattern review's own evidence query (category, record type, time basis,
  hours, locality, minimum confidence, status), with only the period replaced. Station and beat
  filters are dropped; the area is defined by cells. The comparison setting is `NONE`.
- **Periods:** a baseline (before) and an after period, each at most a year. The baseline must end
  before the action starts; the after period must start on or after the action start and end today
  or earlier (no partial counts).
- **Action area:** records with a usable location in the action's cells (same grid size as the
  evidence).
- **Surrounding ring:** records in the cells that touch the action's cells but are not among them
  (one hexagon ring, the six axial neighbours of every action cell).
- **Comparison area** (optional):
  - `CELLS`: records in the given cells (same region, not among the action's cells), counted on
    their own grid size;
  - `STATION`: the `record_count` of one other station of the region for the same query and period.
- **Per 30 days:** each count ÷ days in the period × 30 (2 decimals), so periods of different length
  can be set side by side.
- The summary gives, for each area, before → after per 30 days and the percentage change ("no
  baseline" when the before value is 0).

### 6.2 Denominators and privacy

There is no population or exposure denominator; "per 30 days" is a time normalisation only. The
ADR-012 workbench rule applies: masked records are not in cell counts. A `STATION` comparison uses
the headline `record_count`, which includes the station's combined sensitive total (if at least 3)
and records **without a usable location**, so it is not counted on the same footing as the cell
areas. Each result records its coverage; readers without that access see "not shown".

### 6.3 Interpretation limits

- These are descriptive recorded counts. They are **not** an evaluation, an effectiveness score or a
  causal estimate, and the response says so.
- A change in the ring is a prompt to consider **displacement** (records moving next door) or
  **diffusion of benefit** (falls next door too); the numbers alone show neither.
- An action that adds patrol presence can **raise** recorded counts (more is seen and recorded)
  as well as lower them.
- Small counts, regression to the mean after a period chosen because it was high, seasonality and
  other changes in the same period are not controlled for.
- The ring has a fixed width of one cell at the evidence grid size; displacement further away is not
  measured.

## 7. AI statistical detectors

Summarised here; the governance, database guarantees, review process and kill switches are in
[ai-governance.md](ai-governance.md). Parameters are those registered in the model registry
(`migrations/0012_ai.py`). All detectors use the latest record date per region as the reference date
(historical demo data), IST day windows, locations with confidence 0.4 or more where a location is
used, and never see sensitive-category or HIGHLY_RESTRICTED records.

| Detector | Method | Key parameters | Source |
|---|---|---|---|
| `dq-anomaly` (location share) | Per station, share of records (report time) with no location or confidence below 0.4 in the last 28 days against the previous 84 days. Baseline share `p0 = (bk + 1) / (bn + 2)`; binomial z-score `z = (k − n·p0) / √(n·p0·(1 − p0))`. | At least 20 records in each window; card when `z >= 3` and the share is at least 5 points above `p0` | `worker/ai.py:dq_anomaly` |
| `dq-anomaly` (source volume) | Per source, daily record counts: mean of the last 7 days against the mean and standard deviation of the preceding days; `z = (mean − recent mean) / (sd / √7)`. | Baseline mean at least 5 a day; card when `z >= 3` | `worker/ai.py:dq_anomaly` |
| `duplicate-suggest` | Pairs in the same region and category within 100 m and 120 minutes (occurrence time, last 90 days). Score `0.4·(1 − distance/100) + 0.3·(1 − gap/120) + 0.15·same locality + 0.15·same reporting station`. | Suggest at score >= 0.7, at most 200; never merged | `worker/ai.py:duplicate_score` |
| `geocode-suggest` | For a record with a locality but no location or confidence below 0.4 (last 365 days): the centre of well-located records (confidence >= 0.8) with the same locality at the same station; spread = mean distance from that centre. | At least 5 supporting records; not suggested above 1.5 km spread; confidence high with 20+ records and spread under 300 m, medium with 10+ and under 600 m, otherwise low (flagged for mandatory checking) | `worker/ai.py:geocode_suggest` |
| `emerging-pattern` | CASE records per 1 km hexagon and category: the last 28 days against the mean of the six previous 28-day windows (168 days). One-sided Poisson upper tail `P(X >= k \| λ)`, with `λ` at least 0.5. | At least 5 records; card when `p < 0.001`; one card per pattern per 28-day block | `worker/ai.py:emerging_pattern`, `poisson_upper_tail` |
| `cluster-explain` | PostGIS DBSCAN on UTM metres, per category, over CASE records of the last 28 days; each cluster described by size, centre (snapped to 250 m), radius, time span and night share (20:00 to 05:59 IST). | `eps` 250 m, at least 8 points | `worker/ai.py:cluster_explain` |
| `briefing-template` | Template text from validated insights only, each cited | n/a | [ai-governance.md](ai-governance.md) §1 |

Every input set is fingerprinted: the run stores the SHA-256 of its aggregated inputs
(`worker/ai.py:input_digest`). Thresholds are hand-set and uncalibrated; every card says so.

## 8. Known limitations

- **Recorded data, not occurrence.** Counts reflect what was reported and recorded: willingness to
  report, patrol presence, station recording practice, ERSS call handling and data quality. A higher
  count can mean more reporting or more patrol activity as well as more incidents.
- **Synthetic data and boundaries.** All records and all region, station and beat boundaries are
  synthetic (ADR-013). No result says anything about a real place.
- **No denominators.** No approved population or exposure denominator exists, so no rate is shown
  and counts are not comparable between areas of different size or population (§3.4).
- **Mixed record types.** The dashboard counts all record types together; the workbench does so only
  when `COMBINED` is chosen, with a caveat.
- **Missing times and locations.** Occurrence-time results exclude records without an occurrence
  time; spatial results exclude records without a usable location; a minimum confidence excludes
  records without a location entirely. The excluded counts are stated, except for records the
  caller may not see.
- **Generalised and snapped locations.** Residential locations (about 250 m) and snapped points
  (250 m cells) limit fine-grained analysis; KDE and 250 m hexagons are affected most.
- **Small-group suppression.** Sensitive categories for masked callers appear only in groups of at
  least 3, differently per screen (per cell on the map, per row on the dashboard, only in the
  headline total in the workbench), so totals can differ between screens and bucket counts can add
  up to less than the headline. Secondary suppression across queries is not attempted.
- **Grid effects.** Hexagon counts depend on the cell size and the grid origin.
- **Hand-set thresholds.** Repeat locations (3 weeks), persistence (top 10%, at least 3), KDE
  (200 records, 80% usable, 500 m bandwidth) and every AI threshold are hand-set and have not been
  calibrated or validated against outcomes.
- **Multiple testing.** The emerging-pattern detector tests many cells × categories; only a strict
  p-value (0.001) limits false alerts, so some alerts are chance. Persistence and the "top" boards
  have no significance test at all.
- **Poisson assumption.** Emerging-pattern p-values assume Poisson counts; overdispersion is not
  modelled.
- **Comparisons.** Change percentages on small counts swing widely; partial month or week buckets
  and periods of different length are flagged but not adjusted.
- **Freshness.** The freshness time is the latest source batch, not a guarantee that every record
  is current.

## 9. Not implemented

| Item | State |
|---|---|
| Forecasting (`forecast-review-priority`) | Disabled; draft model, none of the 8 rollout gates met; the `ai_forecasting` flag cannot be switched on while the model is off ([ai-governance.md](ai-governance.md) §4) |
| Analyst copilot (language model) | Disabled (`503 feature-disabled`); no approved deployment |
| Civic Trends | Refused while `civic_context` is off; no verified civic-context source |
| Rates other than the station-rate path | Not built; no approved denominators in the demo |
| Road-segment / corridor views | Reported as unavailable; no road association in the demo data |
| Near-repeat analysis | Not determined from the code: `NEAR_REPEAT` exists as a pattern review type, but no near-repeat calculation was found in the analytics or worker code |
| Map-image export | Not built (needs server-side map rendering; ADR-035) |
| Vector tiles and response caching | Not built (ADR-019) |
| Secondary suppression across queries | Not attempted (ADR-012, ADR-035) |
| Significance testing for boards, persistence or comparisons | Not built |
