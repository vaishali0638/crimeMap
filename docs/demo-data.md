# Demo / Synthetic Data Policy and Generator Design

> Policy applies from Phase 2 onward. As built in Phase 2: generator `crimemap_api.demo.generator`
> (wrapper `scripts/generate_demo_data.py`), boundaries `crimemap_api.cli seed-boundaries` (wrapper
> `scripts/seed_boundaries.py`), ingestion `crimemap_worker.ingest`. One command does everything in
> Docker: `scripts/compose.sh --profile app --profile demo-data run --rm demo-data`.

## 1. Policy

1. **No real police data** is used anywhere in CRIMEMAP development, testing, demos, or screenshots.
2. **No real personal data**: no real names, addresses, phone numbers, vehicle numbers, or government
   ID values. HIGHLY_RESTRICTED fields contain opaque synthetic tokens (`SYNTHETIC-REF-000123`), never
   invented person names.
3. **No real jurisdictions are implied.** Region outlines are simplified approximations; station and
   beat names and boundaries are fictional and labelled "Synthetic boundary — not an official
   jurisdiction" (ADR-013).
4. **Every screen, export, and map image shows "DEMO / SYNTHETIC DATA"** while running in DEMO or
   SANDBOX mode. Exports also embed it in the watermark and file metadata.
5. **Every synthetic row is flagged** (`is_demo = true`). The application refuses to start in
   PRODUCTION mode if any demo row, demo user, or the mock identity provider is present.
6. **Patterns are illustrative, not predictive.** Synthetic hotspots and trends exist so features can be
   demonstrated; they must not be presented as findings about any real place.
7. **Generation is deterministic** (fixed seed) so tests and demos are reproducible.
8. Sensitive categories are generated at low volume and are subject to the same suppression rules as
   real data would be.

## 2. Geography

| Region | Analysis CRS | Fictional stations (planned count) | Notes |
|---|---|---|---|
| Puducherry | EPSG:32644 | 10 | Largest volume |
| Karaikal | EPSG:32644 | 5 | |
| Mahe | EPSG:32643 | 2 | Different UTM zone — tests region-aware grids |
| Yanam | EPSG:32644 | 2 | |

Station points are placed inside each region outline; station polygons are a seeded Voronoi
subdivision (computed in the region's UTM CRS) clipped to the region; each station is split into 2–4
beats the same way. As built: 4 regions, 19 stations, 50 beats, all `DEMO_ONLY`, with source text
"SYNTHETIC - …". Codes and names are neutral and fictional (`PDY-S01`, "Puducherry Demo Station 01
(Demo)", beats `PDY-S01-B1`), never real station names. Tests check that stations tile their region
and beats tile their station (planar area, 1e-9 tolerance), that each geometry is valid, and that
seeding is deterministic and idempotent.

## 3. Records

Default volume: **200,000** incident records over 24 months ending 30 Sep 2026 (matches the ADR-017
performance target; seed 20261012). `--records N` gives smaller runs; tests use 3,000. About 2 % of
records are re-sent later as version 2 (a source correction), and a second 200-record feed,
`DEMO_SYNTHETIC_LEGACY`, is extracted ten days in the past so the stale-source rule fires. Offence
codes are fictional (`D-xxx` cases, `E-xx` calls, `A-01` accidents, `T-01` traffic) and map to
analytical categories through mapping version `DEMO-2026.1`.

| Aspect | Generation approach |
|---|---|
| Record types | Mostly CASE; smaller shares of ERSS_CALL, ACCIDENT, TRAFFIC_EVENT; OPERATIONAL_OBSERVATION from workflow seeds |
| Categories | The eleven seed analytical categories with fictional, plausible proportions |
| Time | Weekly and seasonal cycles, day-of-week × hour profiles per category, occurrence-to-report delay distribution |
| Space | Background random points within beats + a few deliberate clusters (some persistent, some emerging) to exercise hotspot, persistence, and emerging-pattern features |
| Status | Configured status values with plausible ageing |
| Location precision | Mix of EXACT / APPROXIMATE / GRID / UNKNOWN with confidence scores; a share flagged residential |

## 4. Deliberate data-quality defects

So the data-quality workflow can be demonstrated end-to-end, the generator injects (configurable rates):

| Defect | Rate | Rule (code, severity) |
|---|---|---|
| missing coordinates | 3 % | MISSING_LOCATION (DQ-LOC-001, HIGH) |
| low-confidence geocode (< 0.4) | 5 % | LOW_CONFIDENCE_LOCATION (DQ-LOC-002, MEDIUM) |
| point outside every beat | 0.5 % | OUTSIDE_JURISDICTION (DQ-LOC-003, HIGH) |
| missing occurrence time | 2 % | MISSING_OCCURRENCE_TIME (DQ-TIME-001, MEDIUM) |
| unmapped offence code | 0.5 % | INVALID_CATEGORY_MAPPING (DQ-CAT-001, HIGH) |
| near-duplicate (same source and category, ≤ 50 m, ≤ 30 min) | 1 % | POSSIBLE_DUPLICATE (DQ-DUP-001, MEDIUM; the later record is flagged) |
| record that fails the contract | 0.2 % | dead letter `SCHEMA_INVALID` (field paths only, never values) |
| stale feed (`DEMO_SYNTHETIC_LEGACY`) | 1 source | STALE_SOURCE (DQ-SRC-001, HIGH) |
| unknown reporting station | — | OTHER (DQ-REF-001, HIGH) |
| daily reconciliation does not balance | — | OTHER (DQ-REC-001, HIGH, per source; resolved when the day balances) |

Each injected defect is recorded in the generator manifest. Integration tests assert that every
injected defect raises its issue, that at least 90 % of injected duplicates are flagged, and that
clean records raise issues at no more than 1 %. An incident's `record_quality_status` is FAILED if
it has an open HIGH issue, WARNING if it has any other open issue, and OK otherwise; it is
recomputed whenever an issue changes state, including manual review (trigger, migration 0004).
Seven rules run per incident during ingestion (LOC-001/002/003, TIME-001, CAT-001, REF-001,
DUP-001); SRC-001 runs in the source health check and REC-001 in the daily reconciliation.
Residential locations are generalised before storage (ADR-031).

## 5. Ingestion path

Demo records are not inserted directly. The generator writes signed JSON batches; the **demo adapter**
ingests them through the full pipeline (staging → validation → idempotency → mapping → enrichment → DQ
rules → store → lineage → audit), so the demo exercises the same code path a future approved adapter
would. Re-running ingest on the same batches must produce zero new records. The design is in
ADR-027. On the 200,000-record run, re-ingesting all 42 batches took 1.6 s and changed nothing.

## 6. Validation of generated data

A test asserts that generated output contains no strings matching patterns for phone numbers, Aadhaar-
style 12-digit IDs, PAN-style IDs, vehicle registration formats, or email addresses (other than demo
user emails on a reserved test domain) (`services/api/tests/unit/test_demo_generator.py`), and
that every ingested incident has `is_demo = true` (`tests/integration/test_ingest.py`). The API refuses to start in PRODUCTION while
any demo row, demo identity, or demo feed delivery exists (ADR-029).
