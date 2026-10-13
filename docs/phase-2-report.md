# Phase 2 Delivery Report — Data Foundation, Synthetic Data, Ingestion and Data Quality

**Date:** 2026-10-12 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §9.

> **Audited 2026-10-12** — see [phase-2-audit.md](phase-2-audit.md). The audit raised 52 findings and fixed 46
> of them (among them exact residential points and lineage readable by the API role, an approval
> function that trusted its caller, and batches that a single lax-typed record could abort); 3 more
> are partly fixed. It added
> migration 0004, the adapter registry, scheduled ingestion/health/reconciliation jobs, and 59 Python
> and 3 web regression tests. Figures below are from the original Phase 2 run unless marked; current test
> totals are in the audit (388 passed).
Nothing has been committed or pushed. CI has not run on GitHub because the repository has no
remote. Every CI step was run locally (§4).

## 1. Specification acceptance criteria

| Criterion (spec Phase 2) | Result | Evidence |
|---|---|---|
| Re-running ingest produces no duplicates | **Met** | Live Compose run: all 42 batches of the 204,204-record dataset re-ingested in 1.6 s. Every batch returned `ALREADY_PROCESSED`, with 0 records accepted and 0 issues opened, and the incident count was unchanged (199,773). Tests: a same-file re-run adds nothing, re-sent records in a new batch count as duplicates, and the ingest CLI re-run is a no-op. |
| All demo UI displays "DEMO / SYNTHETIC DATA" | **Met** | The safeguard strip on every app page (Shell), the signed-out page, the Keycloak login theme, the boundaries GeoJSON (`data_label`) and `/data-freshness`. Checked in the browser and with curl. |
| Data-quality issues are generated and visible in a queue | **Met** | 23,458 issues across 7 rule types on the live dataset (§4). The Review Queue (`/review`) shows them to `demo.steward` scoped to Puducherry. Assign and resolve-with-note were done in the browser and audited (seq 73 and 76, chain verifies). The Data Health page shows source health, reconciliation and DQ totals. |
| Spatial joins assign records to valid synthetic boundaries | **Met** | Integration tests: each located incident's beat, station and region all cover its point, and the hierarchy matches. Every inside point has a beat. Every incident has a region (unlocated ones take the reporting station's region). Live: 192,758 of 199,773 incidents have a beat. The rest are missing-location or outside-jurisdiction defects, each with an open issue. 0 incidents lack a region. |

## 2. What was built

| Area | Delivered |
|---|---|
| Schema | Migrations `0002` (boundaries with versions and dual control, reference data, source systems, staging, incidents, locations, lineage, DQ issues, `restricted` schema, RLS, views `current_boundary`, `source_health`, `ingest_reconciliation`) and `0003` (PRODUCTION demo-data guard) |
| Synthetic geography | 4 region outlines (approximate), 19 fictional stations and 50 beats from seeded Voronoi subdivisions in each region's UTM CRS. All `DEMO_ONLY` and labelled SYNTHETIC. Deterministic and idempotent. |
| Generator | `crimemap_api.demo.generator`: 200,000 records over 24 months, clusters (one emerging), version-2 corrections, injected defects with a manifest, fictional offence codes, synthetic tokens only, PII-pattern tests. Signed batches (`crimemap-batch/1`, HMAC-SHA256). |
| Ingestion (ADR-027) | `crimemap_worker.ingest`: signature check → source approval gate → immutable staging with idempotency keys → contract validation (dead letters hold field paths only) → newest-version upsert with spatial enrichment → locations and restricted details → lineage → 7 per-incident DQ rules with auto-resolve and reopen → statistics → one audit event per batch. A source-health check opens and closes STALE_SOURCE issues. CLI `crimemap_worker.ingest_cli`. Compose job `demo-data`. |
| API | `GET /regions`, `/stations`, `/beats`, `/boundaries` (GeoJSON), `/boundaries/{unit}/versions`, `POST /boundaries/{id}/approvals` (ADR-026). `GET /dq/issues`, `/dq/summary`, `PATCH /dq/issues/{id}` (ADR-028). `GET /integrations/sources`, `/integrations/sources/{code}`, `/integrations/reconciliation`, `/data-freshness`. `/auth/me` now returns effective permissions. |
| Web | Review Queue with DQ filters, pagination and assign / resolve / accept / reopen actions (CSRF-protected through the BFF). Data Health page. Permission-aware navigation. The safeguard strip shows the real data-refresh time and flags a stale feed. An explicit message when a session lacks MFA. |
| Tooling | `crimemap_api.cli` commands `seed-offence-map`, `propose-boundary` and `generate-demo`; wrappers `scripts/generate_demo_data.py` and `scripts/seed_boundaries.py`; `CRIMEMAP_DEMO_SIGNING_KEY` in bootstrap and `.env.example`; demo seeding in the `migrate` job only outside PRODUCTION. |

## 3. Defects found and fixed during Phase 2

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | Beat tiling check failed at about 1e-5: the geodesic area of zero-width planar slivers | boundary integration test | Cells are clipped in 4326 against the parent, and tiling is measured as planar area (mismatch about 1e-13) |
| 2 | The worker could not upsert restricted details: `ON CONFLICT DO UPDATE` needs SELECT on every column | ingest integration test | UPDATE then INSERT … WHERE NOT EXISTS, plus a column grant `SELECT (incident_id)` only. Token values stay unreadable to the worker. |
| 3 | The migration round-trip test downgraded the shared test database and wiped data seeded by other modules (5 failures, but only when the whole suite ran) | full-suite run | The round trip now runs in a scratch database prepared like `01-roles.sh` |
| 4 | **User provisioning set `is_demo = true` in every environment**, so every real production user would have been marked as a demo identity (Phase 1 code) | writing the PRODUCTION guard | The flag now follows the environment; test for DEMO and PRODUCTION (ADR-029) |
| 5 | Demo-data policy 5 ("refuse to start in PRODUCTION with demo data") was documented but not enforced | traceability review | `core.demo_data_present()` (migration 0003) and an API start-up check |
| 6 | CI pinned `@redocly/cli@2.62.1`, which is younger than the repository's 7-day `minimumReleaseAge`, so the OpenAPI lint step would fail | running the CI command locally | Pinned to 2.57.0 (30 Sep 2026); the spec lints clean |
| 7 | Composed SQL strings tripped semgrep and bandit (no injection path, but inconsistent with Phase 1's literal-SQL rule) | `scripts/run_security_checks.sh` | Statements are written out as literals; the seeded demo PRNG and token placeholders are annotated |
| 8 | Review page said "no access" when the real cause was a session without MFA (the first sign-in after TOTP enrolment carries `amr = ["pwd"]`) | browser test as demo.steward | The page explains this and tells the user to sign in again |

## 4. Verification (run locally, 2026-10-12)

| Check | Result |
|---|---|
| API unit + integration tests (testcontainers PostGIS) | **317 passed** (1 pre-existing Starlette/httpx deprecation warning) |
| Worker tests | 12 passed |
| Mutation checks | Duplicate rule disabled → its test fails. Source approval gate disabled → 6 tests fail. Both restored. |
| ruff, ruff format, mypy --strict (API, worker) | clean |
| Web: tsc, ESLint, Vitest (62), `next build` | clean |
| OpenAPI regenerated, types regenerated, Redocly lint (2.57.0) | valid |
| `scripts/run_security_checks.sh` (bandit, pip-audit, pnpm audit, gitleaks, semgrep, trivy) | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose: migrations 0002 and 0003 applied, seeding | 73 boundary versions, 25 offence codes |
| Live 200k generation + ingest (`demo-data` job) | 204,204 records in 42 batches: 203,777 accepted, 427 dead-lettered (schema-invalid), 0 unexplained. **99.4 s** ingest (111 s with generation) on the development laptop (Colima, 2 CPU / 4 GB). |
| Live DQ issues | low-confidence 9,533 · missing location 6,068 · missing time 3,871 · possible duplicate 2,050 · unmapped code 988 · outside jurisdiction 947 · stale source 1 |
| Live source health | `DEMO_SYNTHETIC` OK, `DEMO_SYNTHETIC_LEGACY` STALE (extracted 10 days ago); the six government systems are listed as NOT_APPROVED / DISABLED |
| Audit chain (live) | `verify_chain()` ok; one `INGEST_BATCH` per batch; DQ changes audited with before/after state and note length only |
| Browser (demo.steward, password + TOTP) | Review Queue, filters, assign, resolve with note, Data Health, refresh time in the strip — all working |

## 5. Security status

- The API role cannot read staging or restricted data and cannot see HIGHLY_RESTRICTED incidents
  (RLS). It can change only four DQ columns. Boundaries change only through the dual-control
  function.
- Lists are filtered to the territory of the permission in use, not the union of all grants
  (ADR-028). Out-of-scope single records return 404, and the attempt is audited.
- The DQ queue never returns coordinates and masks sensitive categories without
  `incident.sensitive_category.read`.
- Dead letters and audit records never contain submitted values or free-text notes.
- Tampered batches stage nothing and are audited as `INGEST_REJECTED`.

## 6. Commands

See README → Local setup. To generate and ingest the demo dataset:
`scripts/compose.sh --profile app --profile demo-data run --rm demo-data` (`DEMO_RECORDS` sets the
size). To propose a boundary version: `python -m crimemap_api.cli propose-boundary <UNIT> <file.geojson>`,
then approve it with two different people via `POST /api/v1/boundaries/{id}/approvals`.

## 7. Known limitations (honest status)

- **No web screen for boundary approval.** The API and CLI only. Incidents keep the beat they were
  assigned at ingestion; re-assignment after a boundary change is not built (ADR-026).
- DQ issues can be assigned only to oneself. Bulk actions are not built.
- Dead-letter retry and the planned Hypothesis property test for idempotency are not built
  (R-ING-2 is 🟨).
- No per-system adapter code for CCTNS, ERSS-112 and the other government systems. They are
  registered as disabled stubs, and the ingestion gate refuses them.
- `core.retention_policy` is not created yet (R-PRIV-2).
- The first sign-in after TOTP enrolment has no MFA claim, so the user must sign in again. This is
  Keycloak's `amr` behaviour for required actions; a realm-flow change could avoid it.
- The Phase 1 review findings (`docs/phase-1-review-findings.md`) remain open apart from what is
  noted there. In particular: W1/W2 (session races), B17 and I1, `/metrics`, and the object-store
  bucket and identity scope.
- Ingest timing is from a single run on a development laptop, not a benchmark.

## 8. Next phase

Phase 3 (Home Map): region-aware grids, map feature and tile endpoints with precision reduction
(ADR-020), search, incident summary/detail views, and operational records, all built on the Phase 2
tables and RLS.

## 9. Go / no-go

**Go for Phase 3**, with the limitations in §7 recorded. All four Phase 2 acceptance criteria are
met with live and automated evidence. Production readiness is **not** claimed.
