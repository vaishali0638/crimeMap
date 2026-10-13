# Decision Log (Architecture Decision Records)

Format follows the ADR template (Status / Context / Decision / Consequences / Alternatives).
Status values: **Accepted** — follows directly from the master specification;
**Proposed — pending owner confirmation** — a default chosen where the specification leaves a
placeholder or open choice. Proposed decisions are safe defaults and may be changed by the owner.

| ADR | Title | Status |
|---|---|---|
| 001 | Modular monolith API + separate worker | Accepted |
| 002 | Two-layer authorisation: policy engine + PostgreSQL RLS | Accepted |
| 003 | Backend-for-frontend session handling | Accepted |
| 004 | Keycloak development identity provider, mock IdP fallback | Proposed |
| 005 | Spatial storage and regional projected CRS | Accepted |
| 006 | Self-hosted basemap tiles (PMTiles) | Proposed |
| 007 | Region-aware map navigation for non-contiguous regions | Accepted |
| 008 | Append-only, hash-chained audit log | Accepted |
| 009 | Dramatiq for background jobs | Accepted |
| 010 | LLM disabled; deterministic template summaries | Accepted |
| 011 | Contract-first API types (OpenAPI → TypeScript) | Accepted |
| 012 | Small-cell suppression for aggregate views (k = 3) | Proposed |
| 013 | Synthetic boundaries for all four regions | Proposed |
| 014 | No denominator → "rate unavailable" | Accepted |
| 015 | GitHub Actions CI and OpenTofu IaC | Proposed |
| 016 | Dedicated repository outside the home-directory repository | Accepted |
| 017 | Performance targets (500 ms p95, 200,000 records) | Proposed |
| 018 | Resource-constrained local profiles (8 GB RAM) | Proposed |
| 019 | Map data delivery: bounded GeoJSON first, vector tiles second | Accepted |
| 020 | Location precision reduction for RESTRICTED map views | Proposed |
| 021 | SeaweedFS as the S3-compatible object store (MinIO substitution) | Accepted |
| 022 | Colima as the local container runtime | Accepted |
| 023 | Route-requirement discovery via FastAPI's public route iteration | Accepted |
| 024 | Grant validity is decided by the database clock | Accepted |
| 025 | Web CSP form-action and Referrer-Policy for the OIDC flow | Accepted |

---

## ADR-001: Modular monolith API + separate worker

**Status:** Accepted

**Context.** The specification lists many functional areas (identity, geography, incidents, analytics,
workflow, AI, reports, audit, integrations). The build team is small and the deployment target is a
laptop-scale demo with a path to a government data centre. Every extra network hop is another place
where authorisation must be enforced and audited.

**Decision.** One FastAPI application (`services/api`) organised into internal modules with explicit
boundaries (`identity`, `geo`, `incidents`, `analytics`, `workflow`, `ai`, `reports`, `audit`,
`integrations`), plus one Dramatiq worker process (`services/worker`) that shares the domain package.
Modules communicate through Python service interfaces, never by reaching into each other's tables.

**Consequences.**
- Positive: one authorisation layer, one audit pipeline, simple local setup, simple transactions.
- Negative: modules scale together; a heavy analytics job could affect API latency (mitigated by moving
  heavy jobs to the worker and using `statement_timeout`).
- Neutral: modules can be extracted into services later if load or ownership demands it.

**Alternatives considered.** Microservices per domain — rejected: high operational cost, repeated
security enforcement, no scale need. Serverless functions — rejected: poor fit for PostGIS sessions,
on-premise government hosting, and long analytics jobs.

---

## ADR-002: Two-layer authorisation — policy engine + PostgreSQL row-level security

**Status:** Accepted

**Context.** The specification requires deny-by-default RBAC + ABAC (role, geographic scope,
classification, purpose) enforced at API, service, database, export, and UI levels — never only in
the frontend. The most likely real-world failure is a single forgotten filter (an IDOR).

**Decision.**
1. **Policy engine** (`services/api/.../policy`): a single function `authorize(subject, action,
   resource, context) → Decision` used by every route through a FastAPI dependency. Every route must
   declare a permission code; a startup check and a test fail if any route lacks one.
2. **Database row-level security**: per request, the API opens a transaction and sets
   `SET LOCAL app.user_id`, `app.scope_station_ids`, `app.scope_region_ids`, `app.max_classification`.
   RLS policies on scoped tables filter on these settings. Tables use `FORCE ROW LEVEL SECURITY`.
3. The API connects as `crimemap_app`, a **non-owner** role without `BYPASSRLS`. Migrations run as a
   separate `crimemap_migrator` role. The worker uses `crimemap_worker` with explicitly granted scope.
4. Highly restricted columns live in the `restricted` schema; `crimemap_app` has no grant on it.
   Access goes through a narrow, audited service using a separate role.

**Consequences.**
- Positive: a missing filter in application code does not leak data; the database is the final gate.
- Negative: RLS adds query planning cost and needs careful indexing on scope columns; tests must run on
  real PostgreSQL (not SQLite).
- Neutral: aggregate analytics run through security-definer functions that apply scope and
  suppression explicitly (see ADR-012).

**Alternatives considered.** Application-only filtering — rejected (single point of failure).
External policy engine (OPA) — deferred; the in-process engine is simpler and testable, and the policy
table format can be ported to OPA/Rego later.

---

## ADR-003: Backend-for-frontend session handling

**Status:** Accepted

**Context.** Access tokens held in browser JavaScript are exposed to XSS. The specification requires
short-lived tokens, rotating refresh tokens, and HttpOnly/SameSite cookies.

**Decision.** Next.js route handlers act as a backend-for-frontend (BFF). The OIDC authorisation-code
flow with PKCE runs server-side; tokens are stored server-side (encrypted session in Redis) and the
browser only holds an opaque `HttpOnly; Secure; SameSite=Strict` session cookie. Access tokens live ≤ 5 minutes; refresh tokens are
**rotated on every use with reuse detection** (Keycloak "revoke refresh token", max reuse 0), so a
stolen refresh token is invalidated on its next legitimate use. The BFF forwards
requests to FastAPI with the access token and a `X-Request-ID`. FastAPI independently validates the
JWT (issuer, audience, signature, expiry, `acr`/`amr` for MFA). State-changing requests require a
CSRF token plus an `Origin` check.

**Consequences.** Positive: no tokens in the browser; one place to enforce re-authentication.
Negative: the BFF is an extra hop and must not become a second, weaker authorisation layer — it only
forwards; all decisions are made by FastAPI.

**Alternatives considered.** SPA holding tokens in memory — rejected (XSS exposure, refresh complexity).

---

## ADR-004: Keycloak development identity provider, mock IdP fallback

**Status:** Proposed — pending owner confirmation

**Context.** The specification names Keycloak for development and requires MFA-ready login. The
development Mac has 8 GB RAM; Keycloak uses roughly 0.5–1 GB.

**Decision.** Use a Keycloak realm (`crimemap-demo`) imported from a versioned JSON file, with one demo
user per role, TOTP required for case-data and privileged roles, and short token lifetimes. Provide a
**mock OIDC provider** compose profile (same token claims, no MFA prompt, labelled DEMO) only as a
fallback if memory is insufficient. The API code is identical for both; production uses an approved
government IdP.

**Consequences.** Positive: realistic OIDC/MFA flows from Phase 1. Negative: memory pressure on 8 GB;
realm configuration must be kept in sync with role definitions (generated from the same role table).

**Implementation (Phase 1, verified in a browser).** Realm `crimemap-demo` (Keycloak 26.8.0) uses a
custom browser flow: username/password then a **mandatory** OTP form for every user. Executions carry
`default.reference.value` = `pwd` / `otp`, mapped into the access token's `amr` claim by
`oidc-amr-mapper`; the API treats `otp` in `amr` as MFA. Two Keycloak behaviours found during testing:
1. `default.reference.maxAge` defaults to **0**, which makes every `amr` value expire immediately, so
   tokens carried an empty `amr`. It is set to the SSO session maximum (28,800 s); a realm test
   enforces this.
2. On the sign-in where a user **enrols** TOTP (required action), the code is entered on the
   enrolment page, not the OTP login step, so that token has `amr = ["pwd"]`. Case-data permissions
   are therefore denied (`MFA_REQUIRED`) until the user signs in again with the authenticator — the
   system fails safe. A UI prompt for this is a Phase 3 usability item.

---

## ADR-005: Spatial storage and regional projected coordinate systems

**Status:** Accepted

**Context.** The four regions are geographically separated and fall in **different UTM zones**:
Puducherry (~79.8°E), Karaikal (~79.8°E) and Yanam (~82.2°E) are in UTM zone 44N (78–84°E); Mahe
(~75.5°E) is in UTM zone 43N (72–78°E). Distance, area, grid, and density calculations in degrees are
distorted; one projection for all regions would distort Mahe.

**Decision.** Store all geometry as `geometry(…, 4326)` with GiST indexes. Each region row records its
analysis CRS (`EPSG:32644` or `EPSG:32643`). Metric operations (grids, hexagons via
`ST_HexagonGrid`/`ST_SquareGrid`, buffers, KDE bandwidths, near-repeat distances, DBSCAN eps) transform
into the region's CRS, compute, and return results in 4326. Cross-region operations never build a
single continuous grid.

**Consequences.** Positive: correct metric results per region. Negative: queries must be region-aware;
helper SQL functions and tests are required.

**Alternatives considered.** `geography` type everywhere — rejected for grids/KDE (no planar grid
functions). Single UTM 44N — rejected (Mahe distortion).

---

## ADR-006: Self-hosted basemap tiles (PMTiles)

**Status:** Proposed — pending owner confirmation

**Context.** Requesting tiles from a public tile server reveals which areas police users are viewing
(an information-disclosure risk) and public OpenStreetMap tile servers have usage policies that
prohibit heavy use. The specification forbids hard-coded proprietary keys and requires the tile
provider to be configurable.

**Decision.** Serve a Protomaps PMTiles extract covering the four regions from the application's own
infrastructure (static file served by NGINX or the web app, range requests). Configure through
the `CRIMEMAP_MAP_STYLE_URL` environment variable (a style that names its own tile sources), so an approved government basemap can replace
it. OpenStreetMap attribution (ODbL) is displayed. The PMTiles file is a build artefact, not committed.

**Consequences.** Positive: no third-party calls from the map; works offline. Negative: the extract must
be produced and refreshed (scripted in Phase 3); file size (tens of MB) for four small regions.


**Phase 3 status.** No PMTiles extract has been produced (it needs a download of OpenStreetMap
data that this environment has not done). The Home Map therefore ships with **no basemap by default**
and draws only the synthetic boundaries; an approved basemap is configured with
`CRIMEMAP_MAP_STYLE_URL`, whose origin is then the only third-party origin the CSP allows (ADR-033).

---

## ADR-007: Region-aware map navigation for non-contiguous regions

**Status:** Accepted

**Context.** A single map extent spanning Mahe (west coast) to Yanam (east, Andhra Pradesh) would show
mostly non-Puducherry territory at a zoom where no detail is visible.

**Decision.** Maps open on one region (the user's home region, or Puducherry). A region switcher and a
small locator inset showing all four regions let the user jump between them. "All Regions" views use
per-region small multiples or the locator, not a single stretched extent.

---

## ADR-008: Append-only, hash-chained audit log

**Status:** Accepted

**Decision.** `audit.audit_event` is insert-only for all application roles (no `UPDATE`/`DELETE`
grants; a trigger rejects modification). Each row stores `prev_hash` and `row_hash =
SHA-256(prev_hash || canonical_json(row))`, giving tamper evidence. The table is partitioned by month;
retention is configurable with a 180-day minimum. Events are also emitted as structured logs for
off-host shipping in production. Audit writes for successful sensitive reads happen in the same transaction as
the read; if the audit write fails, the request fails (fail-closed). Denials and errors are written in
a separate, independent transaction so that a rolled-back request cannot erase the record of the
attempt.

**Consequences.** Hash chaining serialises inserts per chain; mitigated by one chain per partition and a
short critical section. Verification job runs daily.

**Implementation (Phase 1).** A single chain (`audit.chain_head`, one row) is extended by a
`SECURITY DEFINER` `BEFORE INSERT` trigger that assigns `seq`, `prev_hash`, and `row_hash =
SHA-256(prev_hash ‖ canonical JSON)`. The canonical JSON is the row minus hash columns with nulls
stripped (so later nullable columns do not change older hashes) and is rendered with `TimeZone =
UTC`. The application role holds column-level `INSERT` only on non-chain columns, so it cannot set
`seq`, `occurred_at`, or hashes (no backdating). To keep the chain lock short, the API buffers audit
events and inserts them as the **last statements before commit**; a rolled-back transaction also
rolls back the chain head, so `seq` has no gaps. `audit.verify_chain()` reports `gap`,
`broken_link`, `hash_mismatch`, or `tail_missing`. The schema owner can still bypass triggers;
production must ship events off-host and separate the owner role (residual risk, threat model §9).

---

## ADR-009: Dramatiq for background jobs

**Status:** Accepted (the specification prefers Dramatiq if simpler)

**Decision.** Dramatiq with the Redis broker for ingest, data-quality rules, aggregation refresh,
report rendering, notifications, and model jobs. Retries with exponential backoff, a dead-letter queue,
and idempotency keys. Scheduled jobs via a small scheduler process (APScheduler or `dramatiq-crontab`).

**Alternatives considered.** Celery — rejected for this scope (heavier configuration, more moving parts).

---

## ADR-010: LLM disabled; deterministic template summaries

**Status:** Accepted

**Decision.** `AI_LLM_MODE=disabled` is the default and the only mode built in early phases. Analyst
briefings and summaries are produced by deterministic templates from cited query results. An
`LLMProvider` interface exists with a `DisabledProvider`; any future provider must pass the
authorisation, redaction, and citation tests before it can be enabled, and must never receive
RESTRICTED or HIGHLY_RESTRICTED case content unless it is an approved on-premise deployment.

---

## ADR-011: Contract-first API types

**Status:** Accepted

**Decision.** FastAPI/Pydantic v2 generate the OpenAPI 3.1 document. CI exports it, lints it, and
generates TypeScript types into `packages/types` (`openapi-typescript`). The frontend validates
untrusted input with Zod. A CI check fails if the committed types differ from the generated ones.

---

## ADR-012: Small-cell suppression for aggregate views

**Status:** Proposed — pending owner confirmation

**Context.** Aggregated maps and boards can still identify an individual case when a cell contains one
or two records (for example a single sexual-offence case in a small beat).

**Decision.** For roles limited to aggregate views (SENIOR_LEADERSHIP, DEMO_USER) and for every CSV
export, counts below `SMALL_CELL_THRESHOLD` (default **3**) for categories marked sensitive (crimes
against women, crimes against children, and any category flagged by a data steward) are shown as
"< 3" and excluded from point/cell geometry. Totals are adjusted so suppressed values cannot be
recovered by subtraction (secondary suppression or rounding). The threshold is configurable and
documented in `analytics-methods.md`.


**Phase 3 status (implemented, revised by the Phase 3 audit).** For callers without
`incident.sensitive_category.read` covering the whole selection, sensitive categories are combined
into one "sensitive" count, shown per cell (hexagons, beats) or for the whole selection (points)
only when it is at least 3, for every role. Sensitive records not shown that way are **counted
nowhere**: not in the selected, usable, no-location or outside totals. The response then says the
same thing ("sensitive records are counted only in groups of 3 or more", no number) whether 0, 1 or
2 were left out. The first version gave "0" in one case and "fewer than 3" in the other, which a
narrowed query could tell apart, and it added masked records to the no-location and outside
counts unthresholded. Such callers cannot filter on a single sensitive category (an audited 403;
those categories are not offered), because one category alone is not "combined". Search locality
counts follow the same rule. Residual risk, accepted for now: comparing different hexagon sizes, or
overlapping queries, can narrow where a small group lies (secondary suppression across queries is
not attempted). Tested in `test_home_map.py` and `test_phase3_audit.py` (mutation-checked).

---

## ADR-013: Synthetic boundaries for all four regions

**Status:** Proposed — pending owner confirmation

**Context.** No approved official boundary data is available, and real station jurisdictions must not
be implied.

**Decision.** Region outlines are simplified, hand-checked approximations of each region's general
extent, stored with `source = 'SYNTHETIC'` and `approval_status = 'DEMO_ONLY'`. Station and beat
polygons are generated by subdividing each region outline (seeded Voronoi on fictional station
points, clipped to the region). Station names are fictional. The UI labels all boundaries
"Synthetic boundary — not an official jurisdiction". The boundary-versioning model is the same one
that approved boundaries will use later.

---

## ADR-014: No denominator → "rate unavailable"

**Status:** Accepted (specification requirement)

**Decision.** Rates require an approved denominator record (population, registered vehicles, road
length, etc.) with source, period and approval status. If none exists for the selected scope, the
board shows count ranking with a visible "Rate unavailable — no approved denominator" label. No
synthetic denominator is ever presented as real; a clearly labelled synthetic denominator may exist only
in tests to exercise the rate code path.

---

## ADR-015: GitHub Actions CI and OpenTofu IaC

**Status:** Proposed — pending owner confirmation

**Decision.** GitHub Actions workflows (lint, type-check, unit, integration with PostGIS/Redis service
containers, OpenAPI lint and type-drift check, semgrep, bandit, gitleaks, pip-audit, pnpm audit, trivy
image and IaC scan, syft SBOM, Alembic migration up/down check). OpenTofu (open-source Terraform fork)
for reference infrastructure. Kubernetes manifests are reference-only until a hosting platform is
approved.

**Supply-chain controls (Phase 1).** GitHub Actions are pinned to full commit SHAs (version in a
comment). pnpm enforces `minimumReleaseAge: 10080` (no package version younger than 7 days),
`blockExoticSubdeps: true`, and `trustPolicy: no-downgrade`; dependency install scripts are denied
unless listed in `allowBuilds`. Applying the 7-day rule moved five direct dependencies back to the
newest compliant release in the same major (e.g. Next.js 16.4.0 → 16.3.8). Scanner container images
are pinned by tag; pinning by digest is an open item.

---

## ADR-016: Dedicated repository outside the home-directory repository

**Status:** Accepted

**Context.** The user's home directory (`/Users/vaishali`) is itself a git repository that contains
unrelated projects and untracked dotfiles that may hold credentials.

**Decision.** CRIMEMAP lives in its own repository at `~/Documents/crimemap_claude` with a strict
`.gitignore` for secrets. No commits are made to the home-directory repository.

---

## ADR-017: Performance targets

**Status:** Proposed — pending owner confirmation (fills `[TARGET_MS]` and `[TARGET_RECORD_COUNT]`)

**Decision.** Map filter requests p95 < **500 ms** for **200,000** indexed synthetic incident records,
measured with k6 against the local Compose stack at 10 concurrent virtual users. The target applies to
**uncached** requests (response cache disabled for the test); cached results are reported separately
for information only. Analytics overview p95 < 1,500 ms.


**Measured (Phase 3, 2026-10-12, development laptop; Colima VM 2 CPU / 4 GB; PostgreSQL limited to
768 MB; 199,773 incidents).** In-process harness `services/api/scripts/bench_map.py` (not k6):

| Query | 1 user p95 | 10 concurrent p95 |
|---|---|---|
| Hexagons 500 m, Puducherry, 24 months (107k records) | 491 ms | 3,367 ms |
| Hexagons 250 m, Puducherry, 90 days | 199 ms | 1,657 ms |
| Beat totals, Puducherry, 12 months, one category | 72 ms | 479 ms |
| Points, one station, 90 days | 99 ms | 554 ms |

The 500 ms target is met for single users and for beat aggregation at 10 users; it is **not met** for
hexagons over long periods at 10 concurrent users on this hardware (CPU-bound: two vCPUs serve ten
full-region scans). Options, not yet taken: pre-aggregated grid tables maintained at ingestion,
vector tiles (ADR-019), a scope-keyed response cache, or more CPU. The target stays "proposed".


**Measured again (Phase 8, 2026-10-13, same hardware and data; 199,773 incidents).** Same harness,
10 concurrent requests × 6 rounds, uncached, after `jit=off` (see below):

| Query | 1 user p95 | 10 concurrent p95 | Target |
|---|---|---|---|
| Hexagons 500 m, Puducherry, 24 months | 607 ms | 2,949 ms | not met |
| Hexagons 250 m, Puducherry, 90 days | 240 ms | 1,846 ms | not met |
| Beat totals, 12 months, one category | 94 ms | 554 ms | not met (10 users) |
| Points, one station, 90 days | 142 ms | 554 ms | not met (10 users) |
| Points, 30 days, view box | 111 ms | 689 ms | not met (10 users) |
| Analytics overview, Puducherry, 12 months | 637 ms | 3,024 ms | not met (target 1,500 ms) |

The database saturates both vCPUs at 10 users (sampled 170–195 % CPU); the harness used under 80 %
of one host core. Turning off PostgreSQL JIT compilation (`-c jit=off` in Compose) lowered the
500 m hexagon p95 at 10 users from 2,975 to 2,601 ms in one A/B run; the full-suite comparison is
within run-to-run noise, so the gain is small and uncertain. **The targets remain unmet on this
hardware and stay "proposed".** Next options, unchanged: pre-aggregated cells maintained at
ingestion, vector tiles, a scope-keyed cache (reported separately, not counted toward the uncached
target), or more database CPU. Measuring on production-sized hardware is a promotion gate
(deployment.md §3).

---

## ADR-018: Resource-constrained local profiles

**Status:** Proposed — pending owner confirmation

**Decision.** Docker Compose profiles: `core` (postgres+postgis, redis, api, worker, web), `identity`
(keycloak) or `mockidp`, `storage` (minio), `observability` (otel-collector, prometheus, grafana — off
by default). Memory limits per container. The web app runs on the host in development (`pnpm dev`) to
save memory.


**Load finding (Phase 3).** Ten concurrent map queries got the 512 MB PostgreSQL container
OOM-killed (parallel workers each load PostGIS). The Compose stack now caps
`max_parallel_workers_per_gather=1`, sets `work_mem=8MB` and gives PostgreSQL 768 MB; peak usage
under the load test was 660 MB with no restarts.

---

## ADR-019: Map data delivery — bounded GeoJSON first, vector tiles second

**Status:** Accepted

**Decision.** `/api/v1/map/features` returns scope-filtered, bounded GeoJSON (max bbox area, max
feature count, aggregated when above a threshold) for Phase 3. `/api/v1/map/tiles/{z}/{x}/{y}` using
`ST_AsMVT` is added if performance testing shows it is needed. Any tile or response cache key includes
the user's scope hash and classification ceiling, so cached data never crosses scopes.


**Phase 3 status (implemented).** `GET /map/features` returns bounded GeoJSON for one region:
points up to 3,000 within the view (`bbox`), otherwise hexagons with the reason stated. No tiles or
response cache yet, so there is no cross-scope cache risk.

---

## ADR-020: Location precision reduction for RESTRICTED map views

**Status:** Proposed — pending owner confirmation

**Decision.** Exact coordinates are returned only to roles with an explicit `incident.location.exact`
permission (CRIME_ANALYST, SHO and DISTRICT_OR_REGION_SUPERVISOR within scope; DATA_STEWARD only with
declared purpose `DQ_REVIEW`, because geocode review needs the exact point). Other roles receive locations snapped
to a 250 m grid in the region's projected CRS. Records classified HIGHLY_RESTRICTED or with a
residential location flag are never returned as points; they appear only in aggregates. Popups show
locality text, not coordinates.


**Phase 3 status (implemented).** Exact coordinates only inside the scope of
`incident.location.exact` with its conditions; otherwise the centre of the record's 250 m cell in
the region's UTM zone. The steward's grant needs `purpose=DQ_REVIEW` (audited) and, since the Phase 3
audit, applies only to records with an open location data-quality issue ("for geocode review"),
not to the whole region. A view box selects points by the coordinate the caller receives, so it
cannot be narrowed to find an exact point (audit finding, critical). Operational-record points
follow the same rule (exact for their author and exact-location scope only). Residential (generalised
since ADR-031) and HIGHLY_RESTRICTED records are never points. Popups never contain coordinates.

---

## ADR-021: SeaweedFS as the S3-compatible object store (MinIO substitution)

**Status:** Accepted

**Context.** The specification names "S3 / MinIO". When Phase 1 was built (2026-10-11), the
`minio/minio` repository no longer existed on Docker Hub and the quay.io copy required
authentication, so the community MinIO server image was not obtainable without credentials.

**Decision.** Use SeaweedFS 4.48 (`chrislusf/seaweedfs`, Apache-2.0, multi-arch) through its S3 API
in development, behind the `storage` Compose profile. Application code will use a generic S3 client
(Phase 5), so any S3-compatible store (MinIO, Ceph RGW, a government object store) can replace it by
configuration.

**Consequences.** No MinIO-specific features are used. S3 credentials are generated by
`scripts/dev_bootstrap.sh` into `secrets/seaweedfs-s3.json` (git-ignored).

---

## ADR-022: Colima as the local container runtime

**Status:** Accepted

**Context.** Phase 0 assumed Docker Desktop. The development Mac actually has the Homebrew Docker CLI
and Colima (no Docker Desktop, no `docker compose` plugin; standalone `docker-compose` 5.5).

**Decision.** Run containers in a Colima VM (`colima start --cpu 2 --memory 4 --disk 30 --vm-type vz`).
`scripts/compose.sh` uses whichever Compose is installed. Testcontainers needs
`DOCKER_HOST=unix://$HOME/.colima/default/docker.sock` and
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`; `TESTCONTAINERS_RYUK_DISABLED=true`
is used locally because the Ryuk image could not be pulled on the available network (the test
fixture stops its own container).

---

## ADR-023: Route-requirement discovery via FastAPI's public route iteration

**Status:** Accepted

**Context.** FastAPI 0.143 no longer copies included routers' routes into `app.routes`; it stores
`_IncludedRouter` wrappers. The first version of the startup check iterated `app.routes`, found no
API routes, and would have passed vacuously.

**Decision.** Discover routes with the public `fastapi.routing.iter_route_contexts`, and refuse to
start if **no** API routes are discovered. A unit test pins the exact route → requirement table, so a
framework change that alters discovery fails CI immediately.

---

## ADR-024: Grant validity is decided by the database clock

**Status:** Accepted

**Context.** Grants are written with the database's `now()`. The API first re-checked `valid_from`
against its own host clock; with the Colima VM about one second ahead of the host, a grant created
moments earlier looked "not yet valid" and 18 integration tests failed. Any database/API clock skew in
production would cause the same.

**Decision.** `load_role_grants` returns only grants active by the **database** clock
(`valid_from <= now()` and not expired). The policy engine still receives `valid_to`, so a grant that
expires mid-request is not used. Integration tests cover just-granted, scheduled (future), and expired
grants.

---

## ADR-025: Web CSP form-action and Referrer-Policy for the OIDC flow

**Status:** Accepted

**Context.** Browser testing found two policies that broke sign-out:
1. `Referrer-Policy: no-referrer` makes browsers send `Origin: null` on same-origin form POSTs, which
   the CSRF origin check correctly rejected.
2. `form-action 'self'` is applied by browsers to the redirect that follows a form submission, so the
   BFF's redirect to the identity provider's end-session endpoint was blocked.

**Decision.** Web pages use `Referrer-Policy: same-origin` (no referrer is ever sent to another site)
and `form-action 'self' <identity-provider origin>`. The API keeps `no-referrer` (it serves no
documents). Both are covered by unit tests (`tests/headers.test.ts`, `tests/csp.test.ts`).

---

## ADR-026: Boundary versions take effect only through a dual-control database function

**Status:** Accepted (Phase 2)

**Context.** The specification requires boundary versioning with approval. A changed station or beat
boundary changes which territory every incident belongs to, so one person must not be able to do it
alone. The API role should not hold UPDATE on boundaries.

**Decision.** New versions are stored as `PROPOSED` (`crimemap_api.cli propose-boundary`, which
rejects invalid polygons and polygons more than 0.1 % outside the parent's current boundary). They
come into force only through `POST /api/v1/boundaries/{id}/approvals`, which calls the SECURITY DEFINER
function `core.approve_boundary()`. The API decides which slot the caller may fill: `SYSTEM_ADMIN`
(`admin.config`) or `DATA_STEWARD` (`dq.manage` in the boundary's region). Both slots need a fresh
login. The function enforces, atomically and under a row lock, that each slot is filled once, by two
different people (also a CHECK constraint), and that the old version is retired when the second
approval lands. Every approval and refusal is audited.

**Consequences.** Incidents ingested earlier keep the beat they were assigned when they were
ingested. Lineage records the beat boundary version that was used. Re-assigning history to a new
boundary is a separate, auditable job, deferred until a real boundary change needs it.


**Amended (Phase 2 audit, migration 0004).** The function no longer takes the approver as a
parameter: it uses the session's `app.user_id` (set by the API's unit of work), refuses an inactive
user or one without an active, unrevoked SYSTEM_ADMIN grant (ADMIN slot) or a DATA_STEWARD grant
covering the boundary's region (STEWARD slot), and raises CRIMEMAP's own SQLSTATEs (`CM001`
separation of duties, `CM002` not authorised) so a genuine privilege error is never reported as a
separation-of-duties refusal. The API checks the slot's permission and a fresh login before it
reveals whether a boundary exists, and audits refusals including unknown ids. Proposals made with
`propose-boundary` are audited (`BOUNDARY_PROPOSED`) in the same transaction.

---

## ADR-027: Ingestion runs as one set-based transaction per signed batch

**Status:** Accepted (Phase 2)

**Context.** The pipeline must be idempotent, keep raw payloads immutable, route invalid records to a
dead-letter queue, keep lineage, and audit each batch, at a 200,000-record scale. Review finding B1
(Phase 1) showed that a long transaction holding the audit-chain lock blocks every API audit write.

**Decision.**
- Batches are signed (HMAC-SHA256, format `crimemap-batch/1`). A bad signature stages nothing and is
  audited as `INGEST_REJECTED`.
- Each batch is processed in one worker transaction with set-based SQL: stage, validate, upsert
  incidents with the newest version winning, add locations and restricted details, write lineage,
  run the DQ rules, record statistics, then write one `INGEST_BATCH` audit event. The audit insert
  comes last, so the chain lock is held only for the final moments of the batch.
- Idempotency: one row per `(source, batch_ref)`; one row per `sha256(source|reference|version)`.
  Re-sending a batch returns `ALREADY_PROCESSED`. Re-sending records in a new batch counts them as
  duplicates.
- The worker can write restricted details but can read only their key column. It uses
  UPDATE-then-INSERT instead of `ON CONFLICT DO UPDATE`, which would need SELECT on every column.
- Rules run only for incidents the batch created or changed. An issue a reviewer marked RESOLVED
  is reopened if a new source version still fails the same rule. ACCEPTED_AS_IS is never reopened.

**Measured.** 204,204 records in 42 batches were ingested in 99 s on the development laptop. A
re-run took 1.6 s and changed nothing (docs/phase-2-report.md).


**Amended (Phase 2 audit, migration 0004).**
- Raw payloads stay exactly as received; the pipeline processes a separate `canonical` column (the
  validated, normalised record). Versions and flags are strict, so a lax value is dead-lettered
  instead of aborting the batch in SQL.
- Record idempotency keys include the content hash: an identical resend is a duplicate; different
  content under an already accepted reference and version is dead-lettered as `VERSION_CONFLICT`;
  a corrected record closes the earlier `SCHEMA_INVALID` dead letter.
- Batches store a content hash. Re-using a batch reference for different content is rejected and
  audited (`BATCH_REF_CONFLICT`); generator references include seed, size and extraction time.
- A failing batch is rolled back, then recorded as FAILED and audited (`INGEST_FAILED`, error type
  and SQLSTATE only) in a separate transaction; directory and inbox runs continue with the next
  file, and a re-delivered FAILED batch is processed.
- Batches of one source are serialised by an advisory lock. DQ rules run for the changed incidents,
  their possible-duplicate partners, and incidents whose open duplicate issue points at them, so a
  pair is found whichever record arrives first. Issues follow their incident's territory.
- Extraction times are clamped to receipt + 5 minutes; signature rejections are counted against the
  claimed source in reconciliation but cannot change a feed's health. The demo signing key can load
  only DEMO sources; an approved source needs its own key, which is not built.

---

## ADR-028: Lists are filtered to the territory of the permission, not only by row-level security

**Status:** Accepted (Phase 2)

**Context.** Row-level security filters by the union of all the caller's grants. A person who is
DATA_STEWARD for Karaikal and CRIME_ANALYST for Puducherry would otherwise see Puducherry's
data-quality queue, a permission they hold only for Karaikal.

**Decision.** `policy.engine.permission_scope(subject, permission)` returns the regions and
stations of the grants that confer *that* permission, counting only grants with MFA and still valid.
List endpoints add it as an explicit SQL filter on top of RLS. Single-record writes also call
`authorize` with the record's territory. A record outside the caller's scope returns 404, so its
existence is not disclosed, and the attempt is audited as a denial. `/auth/me` returns the
effective permission list so the web app can hide links. Every page and endpoint still checks
access itself.

The DQ queue shows only the fields a steward needs to judge data quality: precision, confidence,
geocoding method and times. It never shows coordinates. It masks sensitive analytical categories
unless the caller also holds `incident.sensitive_category.read` for that territory.


**Amended (Phase 2 audit, migration 0004).**
- Row-level security now also hides data-quality issues about records the caller cannot see
  (e.g. HIGHLY_RESTRICTED incidents) and lineage links (`core.source_record_link` had no RLS).
- A REGION/STATION grant with an empty territory confers nothing, even for a non-territorial check
  (closes Phase 1 finding B17); a source-wide issue can be handled by any steward with territory.
- An out-of-scope region filter is refused (403, audited) instead of being silently narrowed.
- Categories: a caller without `incident.sensitive_category.read` sees **no** analytical category
  on any incident in the queue. Flagging only the sensitive ones (as first built) disclosed exactly
  what it was meant to hide. Under this rule the steward's DQ view of a sensitive-category case
  (reference, times, location quality) does not reveal that it is one, which is how the DQ queue
  is reconciled with data-classification §1 ("aggregate-only for roles without
  `incident.sensitive_category.read`"); the reviewer must confirm this interpretation.
- Rule details are whitelisted per rule; a possible duplicate stores the partner's id, and the API
  shows its reference only if the caller may see that record.
- List, summary, refused transition and approval refusals are audited; list audits include the
  issue ids shown; before/after metadata records the previous assignee.

---

## ADR-029: Production refuses to start while demo data is present

**Status:** Accepted (Phase 2)

**Context.** Demo-data policy 5 says the application must refuse to start in PRODUCTION if any demo
row, demo user, or demo feed is present. Phase 2 also found that user provisioning flagged every new
user `is_demo = true` in every environment. That would have marked real production users as demo
identities.

**Decision.** Users are now flagged demo only outside PRODUCTION. Migration 0003 adds
`core.demo_data_present()`, a SECURITY DEFINER function returning one boolean that sees past RLS.
The API's start-up hook raises if it returns true and the environment is PRODUCTION. Every demo seeder
also refuses PRODUCTION itself, and the Compose `migrate` job seeds demo data only outside PRODUCTION.

---

## ADR-030: Adapters deliver; the shared pipeline controls; jobs are scheduled and retried

**Status:** Accepted (Phase 2 audit)

**Context.** The specification asks for a pluggable adapter architecture (demo adapter built
fully, approved-source adapters as stubs only), retries with exponential backoff, a source-health
dashboard, and a daily reconciliation report. Phase 2 first shipped only a CLI over a directory.

**Decision.** `crimemap_worker.adapters` defines `SourceAdapter` (it only *delivers* signed batch
files) with a registry: `SignedFileDropAdapter` for the demo feeds (`<inbox>/<source code>/`) and
`NotApprovedAdapter` stubs for CCTNS, ERSS-112, ICJS, eDAR, e-challan and CCTV/ICCC, which always
refuse. A source that is not DEMO/APPROVED is refused before any adapter is built, and a batch that
declares a different source from the adapter that delivered it is rejected. The scheduler runs
`ingest_inbox_batches` (every 5 minutes, only when `CRIMEMAP_WORKER_INBOX_DIR` is set; off in the
default Compose stack, where the `demo-data` job loads the dataset), `check_sources` (every 5
minutes) and `reconcile_yesterday` (daily), all with Dramatiq retries (5 attempts, 10 s doubling to
10 min). Processed, rejected and failed files are moved to `processed/`, `rejected/`, `failed/`.

**Not built.** Per-source signing keys and key rotation; a content/virus-scan step (batches carry
no attachments); dual control for *manual* reconciliation (there is no manual reconciliation
function yet).

---

## ADR-031: Residential locations are generalised in the analytical store

**Status:** Accepted (Phase 2 audit)

**Context.** Data-classification §1 treats exact residential locations as HIGHLY_RESTRICTED. Phase 2
stored them as exact points in `core.incident_location` with only an `is_residential` flag, readable
by the API role through RLS for any visible incident (the reviewer counted 18,916 EXACT residential
points in the live demo database).

**Decision.** The exact point is kept only in `restricted.sensitive_source_detail.residential_location`
(no API access). The analytical row holds a point snapped to a ~250 m grid, moved into the assigned
beat if snapping left it (`core.generalise_point`), precision `GRID`, and no free-text location.
Beat/station/region are assigned from the exact point before generalisation. A CHECK constraint
(`residential_is_generalised`) makes it impossible to store an exact residential point in core.
Migration 0004 converted all existing residential points with coordinates (27,697 on the live demo
database, verified after migration; none left outside its beat).

**Consequences.** Hotspot and duplicate analysis on residential incidents works at grid
resolution. The map's precision reduction (ADR-020) remains as a second, presentation-level control.

---

## ADR-032: One selection, metric coordinates and cheap row-level security for the Home Map

**Status:** Accepted (Phase 3)

**Context.** HM-1 requires that counts, legend and status bar always agree. ADR-017 asks for
map queries over 200,000 records well under a second. The first measurements were 9.6 s
(`ST_HexagonGrid` joined to points) and 0.9 s (on-the-fly `ST_Transform`).

**Decision.**
- `core.map_selection(...)` (SQL, STABLE, SECURITY INVOKER, inlinable) is the single definition of
  "records this query covers". Every map statement reads it, so counts cannot drift. Row-level
  security applies inside it.
- `core.incident_location` stores the point in its region's UTM zone (`analysis_x/y/crs`), kept by a
  trigger. Hexagon binning is plain arithmetic on those metres (pointy-top axial coordinates);
  `core.hex_polygon` draws only the cells that exist.
- The location row carries copies of its incident's region, station and classification, kept in
  sync by triggers. Its RLS policy is then a direct predicate instead of a per-row subquery into
  `incident_record`, which had cost 858k buffer hits for 107k rows.
- RLS scope lookups are written as scalar subqueries (`CAST((SELECT core.scope_ids(...)) AS uuid[])`).
  Postgres evaluates them once per query instead of per row (scan 196 → 104 ms).
- Hexagon and beat requests read the selection once. The same grouped rows give the status-bar
  breakdown and the cells.
- Global case-data grants (DEMO_USER, SENIOR_LEADERSHIP) set `app.scope_global`, without which their
  aggregate maps were empty (audit L3). The API still applies each permission's own scope and its
  aggregate-only rule.

**Consequences.** A single 24-month whole-region hexagon query takes about 0.45 s, and beats or
points about 0.1 s. Concurrency limits are in ADR-017. The copied columns are tested for consistency,
including after a region move.

---

## ADR-033: Map rendering without third-party requests

**Status:** Accepted (Phase 3)

**Decision.**
- MapLibre GL JS 6.12 renders the map. Its worker and shared chunk are copied to `/maplibre/` at
  build time (`apps/web/scripts/copy-map-assets.mjs`) and loaded same-origin, so the CSP needs only
  `worker-src 'self'`, with no `blob:` workers and no CDN.
- The default style is a plain background with no glyphs, sprites or tiles. Labels and counts live in
  accessible HTML (legend, status bar, drawer), never only on the canvas.
- `CRIMEMAP_MAP_STYLE_URL` switches to an approved basemap. Only its origin, plus any origins
  listed in `CRIMEMAP_MAP_ASSET_ORIGINS`, are added to `connect-src`/`img-src`. Both must be https
  (http only for localhost outside PRODUCTION). An invalid value turns the basemap off instead of
  failing every page (Phase 3 audit).
- Radix's scroll lock injects a `<style>` element; the root layout passes the request nonce to
  `get-nonce` so that the strict `style-src` allows it (Phase 3 audit).
- HM-6 was verified in the browser: every resource loaded from the app's own origin.
- Browsers suspend `requestAnimationFrame` in hidden tabs, so the map draws only once its tab is
  visible.

---

## ADR-034: PY Dashboard boards read the map's selection, rank neutrally and never invent rates

**Status:** Accepted (Phase 4)

**Context.** The specification asks for a station sidebar, a map, View Details, and seven Trends
boards (Top Crime Spot, Police Station Crime Rate, Region, Location, Time, Month, Week). Each board
must state its filters, definition, quality caveat and the export/logging restrictions. The rate board
must fail safely without a denominator, and the wording must be neutral.

**Decision.**
- **One selection.** Every board, the station details and the dashboard map read
  `core.map_selection` (migration 0007 appends each record's time on the chosen basis and its
  locality). The region board equals the map's selected count for the same filters, and the
  month/week boards add up to it (tested).
- **Permission and territory.** `map.aggregate.read` within its territory, as in the role matrix.
  "All regions" means the regions the caller may see. A named region or station outside them is an
  audited 403.
- **Privacy.** ADR-012 applies per board row and per station: combined sensitive records count in a
  row only as a group of 3 or more. Callers without sensitive access cannot rank a single sensitive
  category, and case-status counts need `incident.summary.read`. Station details combine
  sensitive records for the whole station, the map per cell, so the two totals can differ; the drawer
  says why.
- **Comparison.** Area and time-of-day boards compare with the same-length period immediately before.
  Month and week boards compare with the bucket before, show buckets in date order with a count rank,
  and mark partial buckets.
- **Rates (ADR-014).** A rate per 100,000 residents is shown only from an APPROVED `POPULATION`
  denominator overlapping the period, and ranking is by rate only when every station has one.
  Otherwise the board says "Rate unavailable — no approved denominator. Ranked by record count
  instead." The demo has no denominator; a labelled test-only denominator exercises the rate path.
- **Wording.** Board text says "higher recorded count in the selected period". A test scans the API
  module and the web catalogue so that prohibited labels ("dangerous", "worst", "criminal",
  "high-crime") never appear (PD-6).
- **Audit and export.** Board and details views are audited (`ANALYTICS_QUERY`). The station list is
  reference data and is not audited. Export is not offered on the dashboard, and each board says
  exports need the report permissions and a fresh sign-in (Phase 5 Reports).
- **Map.** One region at a time (ADR-007). A chosen station is fitted, its boundary drawn prominently,
  and its tags drawn on top of the dimmed region tags. Region and station are kept in the URL so a
  view can be restored.

**Consequences.** Measured on the 200k demo set (2 vCPU): Top Crime Spot over all regions for 12
months is about 1.1 s for one user (hexagon binning over two periods); the other boards take
0.02–0.3 s. Pre-aggregation (ADR-017 options) would help the spot board at concurrency.

---

## ADR-035: Analytics Workbench, evidence, pattern reviews and controlled exports

**Status:** Accepted (Phase 5)

**Decision.**
- **One selection, one envelope.** Every analytics statement reads `core.analytics_selection`
  (migration 0008). This is the map selection plus locality, IST time-of-day and the ADR-012 masked
  flag. Every result is `{data, meta}` as in api.md §2: filters applied, record and excluded counts with
  reasons, the "not counted" rule, freshness, method and parameters, data-quality note, caveats,
  classification, environment. The Table view and every export are built from the same aggregated
  tables.
- **Record types.** CASE by default. `COMBINED` must be chosen explicitly and adds a caveat. Case
  status (filter and trend) needs `incident.summary.read` and CASE records.
- **Scope.** `analytics.read` within its territory (`analytics.compare` for Comparison). A data
  steward's grant covers the Data Quality section only. Out-of-scope requests are audited 403s.
- **Privacy (revised by the Phase 5 audit).** For callers without sensitive access over the whole
  region, sensitive records appear in **no** time or place bucket (day, week, cell, unit, hour, delay
  band). They appear only once, as the combined total in the headline count and the category list,
  and only when that total is at least 3. Per-bucket admission let "total minus the bucket sums"
  reveal small groups. Whether records are combined is decided from region-wide grants only, never
  from the data. Residual risk, accepted: comparing separate queries (one station against the region,
  two hour windows) can still narrow numbers; secondary suppression across queries is not attempted. Callers without sensitive access cannot filter on a
  sensitive category, and repeat-location lists never include masked records.
- **Methods**, stated in `method_parameters`:
  - hexagon counts in the region's UTM zone, beat and station choropleths;
  - repeat locations: localities with records in at least 3 distinct weeks;
  - persistence: cells in the top 10% with at least 3 records, compared across both periods, labelled
    in both, current only, or comparison only;
  - KDE: Gaussian, 500 m bandwidth, on 250 m hexagons, records per km², and only with at least 200
    records and at least 80% usable locations, otherwise an explanation.
  - Road-segment views are reported as unavailable, since the demo data has no road association.
- **Not built in this phase.** Civic Trends is refused while its flag is off. AI Analysis is Phase 7.
- **Evidence snapshots.** The server re-runs the analysis from its validated parameters and stores
  data, meta, parameters and a SHA-256 of all four, immutably. Since migration 0009 a snapshot also
  records every unit it covers (both sides of a comparison; a station officer's own stations) and
  whether sensitive categories were combined. Readers other than the author need the section's
  permission over all of it: Data Quality only for stewards, `analytics.compare` for comparisons,
  and sensitive access when the snapshot holds sensitive breakdowns. Exports are authorised over the
  same coverage, so station-scoped SHOs can export their stations. Notes on snapshots follow the
  same rule. Pattern reviews, exports and analysis
  notes cite a snapshot.
- **Pattern reviews.**
  - Created only from a Location Trends hexagon snapshot that has a comparison period.
  - The cells must be in the snapshot.
  - The server derives the record count, baseline count, periods, category, data-quality summary
    and area geometry; the analyst gives the type, narrative and next step.
  - Narratives with personal identifiers or references to individuals are refused.
  - Reviews are saved as DRAFT; supervisor decisions are Phase 6.
- **Exports.**
  - Need `report.export.aggregate_csv` or `.pdf` in the region, a declared purpose, and a sign-in
    within 5 minutes (policy engine, audited refusals). The UI offers "Sign in again".
  - Each export re-runs the analysis, creates a snapshot, and contains aggregated tables only.
  - Each file carries the `INTERNAL / RESTRICTED` watermark (every PDF page) and the required
    metadata. CSV cells are protected against formula injection.
  - Request, generation and every download are audited. Files are readable by their requester only
    (RLS), expire after 24 hours, and are deleted hourly by the worker.
- **Deviations, stated rather than hidden.**
  - (1) Files are stored in PostgreSQL (`core.report_export`, at most 5 MB), not the S3 object store
    of ADR-021: no S3 client exists in the codebase yet. Moving them to object storage is a contained
    change.
  - (2) Downloads go through the authenticated, requester-only BFF/API path instead of a signed URL.
    This is stricter: the link is unusable from another session.
  - (3) The PDF is written by a small dependency-free writer (Helvetica, Latin-1). It was validated
    by macOS PDFKit; there is no third-party PDF library.
  - (4) Map-image export needs server-side map rendering and is not built.
- **Charts.** Apache ECharts (spec), canvas renderer, rich-text tooltips: no HTML with inline styles
  under the strict CSP, and no data parsed as HTML. ARIA descriptions are on; every chart has a
  visible title and the Table view holds the same numbers.


## ADR-036: Review Queue workflow — pattern review decisions, operational actions, outcome reviews

**Status:** Accepted (Phase 6)

**Decision.**
- **State machines, enforced twice.** The API checks permission, territory, separation of duties
  and the allowed transition. Migration 0010 adds triggers that allow only the same transitions, by
  the session user (`app.user_id`), with the required fields, plus CHECK constraints and row-level
  security. A defect in one layer does not open the other.
  - Pattern review: DRAFT → (author, with an assessment) PENDING → (a different person, with a
    rationale) APPROVED, REJECTED, or RETURNED (back to DRAFT for revision and resubmission). The
    evidence (cells, periods, counts, snapshot) is fixed at creation.
  - Operational action: proposed only from an APPROVED review, within its cells, at a station of its
    region. PROPOSED → APPROVED / REJECTED by someone other than the proposer, with a rationale (the
    approver may assign an officer of that station). APPROVED → COMPLETED with completion evidence,
    by the assigned officer (`action.complete` ASSIGNED scope) or the station's SHO. The plan never
    changes after proposal.
  - Outcome review: only for a COMPLETED action, one per action. The server computes the metrics.
    DRAFT → FINAL only with a baseline, at least one evidence reference and a conclusion of 20 or
    more characters (API 422 and a database CHECK). Only the reviewer edits or finalises; FINAL is
    immutable.
- **Audit and history.** Each transition writes a `WORKFLOW_TRANSITION` audit event (states,
  rationale length; never free text) and a `core.workflow_transition` row, in one transaction.
  Refusals are audited (SoD, invalid state, incomplete).
- **Outcome metrics are descriptive.** Recorded CASE counts on the occurrence basis, from the same
  analytics selection and ADR-012 rule as the workbench, under the reviewer's own access: the
  action's cells, the ring of neighbouring cells, and an optional comparison area (a station or
  cells), per period and per 30 days. The summary states that ring changes are a prompt to consider
  displacement or diffusion of benefit, not evidence of either. No causal or effectiveness score.
- **Safety.** Actions are about places and times; no column can name a person, and free text refuses
  personal identifiers and references to individuals. Nothing is triggered automatically.
- **Territory limits are kept.** A station-scoped SHO cannot see a region-wide pattern review or its
  evidence (row-level security), so on such actions the evidence shows as "outside your territory
  view", and the SHO cannot propose from a region-wide review. The region supervisor sees both.
- **Queue.** One function produces the queue items and the badge counts. Priority: HIGH when overdue
  or a pending review has 50+ records. Due dates: decisions 7 days after submission; actions by their
  end date; outcome reviews 28 days after the action ends.

**Alternatives considered.** A generic workflow engine (rejected: three small state machines are
easier to verify, and the database can enforce them directly); approval by role alone without a
database trigger (rejected: one API defect would allow self-approval).

**Consequences.** Station officers see actions assigned to them without the region-level evidence.
Correction workflows (applying approved location corrections) remain unbuilt. AI insights (Phase 7)
are a placeholder tab.

**Revised by the Phase 6 audit (migration 0011, 2026-10-13).**
- History is written by database triggers only (the application role lost INSERT on
  `core.workflow_transition`), and records creation and revisions too.
- The triggers also freeze the supervisor's decision fields during the author's revisions, the
  approval during completion, and the completion fields during approval.
- Independence: the approver does not record completion; the person who completed an action does
  not review its outcome (API 409 and database).
- Completion needs the action to have started (IST). Retroactive actions (dates in the past) remain
  allowed, so a station can record an action already taken.
- Outcome metrics use the pattern review's own evidence query (category, record type, time basis,
  hours, locality, confidence, status); only the period changes. They record their coverage
  (regions, stations, whether sensitive categories were combined), and readers without that access
  see "not shown". The baseline ends before the action starts; the after period ends today or
  earlier; comparison areas are in the action's region, distinct, and outside the action's cells.
- Category labels and evidence links on reviews, actions and queue items follow the evidence
  snapshot rule (ADR-035): shown only with the access that produced the evidence.
- Queues select actionable rows in SQL (state, permission territory, filters), report `more`, and
  filter by priority, region, owned-by-me and overdue. Database refusals map to audited 409s.
- Data-quality issues and operational records show their history from the audit log.

## ADR-037: Responsible AI assistance — explainable detectors, insight cards, registry and kill switches

**Status:** Accepted (Phase 7)

**Decision.**
- **Statistics and rules, not black boxes.** Five detectors deliver the spec's items 1–5 in order:
  - data-quality anomalies (binomial and normal z-scores against each unit's own baseline);
  - duplicate suggestions (weighted rule score);
  - geocoding suggestions (locality centre, support and spread);
  - emerging place-time patterns (Poisson upper tail, 1 km cells, 28 days against 24 weeks);
  - explainable clusters (PostGIS DBSCAN).

  Template briefings deliver item 6. Item 7 (a language model) is an adapter path that is built,
  tested and **disabled**.
- **A database role for AI.** Detectors run as `crimemap_ai` (NOLOGIN). The worker switches to it per
  run. It can read approved columns of non-sensitive, non-highly-restricted records (row-level
  security) and write only runs and insights. The guarantee "AI cannot create an action, export
  data or modify a case" is enforced by grants, not by code review.
- **Insight card contract in the schema.** The database requires evidence, baseline, limitations,
  alternatives, explanation, confidence and calibration note, and data-quality score. The input
  snapshot is the run (parameters, input counts and exclusions, SHA-256 of the aggregated inputs).
  The review is the only change: once, by the session user, with a note. It is audited and shown
  as the card's history.
- **Registry and switches.** `core.model_registry` holds the model cards. A trigger lets the API
  change only the switch, with who and why, and refuses switching on an unapproved model or an
  ML/LLM model with any unmet rollout gate. A global `ai_assistance` flag stops all detectors and
  reviews. `ai_forecasting` and `ai_copilot` cannot be switched on while their model is off.
- **Visibility.**
  - Insights follow `ai.insight.read` territory.
  - A steward's grant covers data-quality types only (as in ADR-035 for analytics).
  - Referenced records are named only to callers who may see them.
  - Sensitive categories never reach a detector.
- **Reference date.** The latest record date per region (historical demo data).

**Alternatives considered.**
- Machine-learning anomaly models: rejected for now. They are not explainable to the standard of
  the card contract, and there are no labels.
- Enforcing AI limits in application code only: rejected, because one code defect would breach them.
- An external LLM: rejected, because there is no approved deployment.

**Consequences.**
- Thresholds are hand-set and uncalibrated; this is stated on every card.
- Validation records judgement only. Corrections still go through the existing workflows.
- The role must exist before migration 0012 (`infrastructure/docker/postgres/init/02-ai-role.sh`,
  idempotent).

**Revised by the Phase 7 audit (migration 0013, 2026-10-13).**
- **Login role.** `crimemap_ai` is a LOGIN used only by the new `ai-worker` container, which holds no
  other credentials. The worker can no longer switch to it, and it cannot switch to another role.
  It writes its own `AI_RUN` audit events.
- **Inputs follow the incident.** The AI role sees a location or data-quality issue only if it may
  see the incident.
- **Writes.** Insights only into a running run of an approved, enabled detector of that type.
- **Flags.** The feature flags have their own database guard: AI flags only, by the session user,
  and forecasting or copilot only while their model is on.
- **Auditors** read insights through `app.ai_global`. Case-data scope is not widened.
- **Detectors.**
  - IST day windows;
  - locations with confidence 0.4 or more;
  - one card per pattern per 28-day block;
  - log-space Poisson tail;
  - station set only when all records share it;
  - failures recorded as FAILED runs and audited;
  - switches re-checked per run.
- **API.**
  - list and badge scoped in SQL;
  - runs open to readers of their insights;
  - briefings for reviewers;
  - fixed refusal messages;
  - rollback state in the registry.
- **Copilot guard.** Broadened; names and addresses still need an approved redaction service
  (a rollout gate).

---

## ADR-038: Observability — Prometheus metrics with audit-derived security signals

**Status:** Accepted (Phase 8)

**Decision.** The API exposes `GET /metrics` in the Prometheus text format, written without a client
library: request counts and a latency histogram labelled by route **template**, and security and
operations signals computed from the audit log over fixed windows (exports, denials, privilege and
configuration changes, first-time region access, ingestion and AI failures, request errors). The
endpoint needs its own bearer token (`CRIMEMAP_METRICS_TOKEN`; 404 when unset) and is not proxied by
the BFF. Signals are counts only, read with the application role and cached for 30 seconds. Alert
rules and a Grafana dashboard are provisioned under `infrastructure/observability/` (Compose profile
`observability`).

**Why.** The specification asks for alerts on mass export, unusual geographic access, repeated
denials, privilege changes, failed ingestion and model failures. Every one of these is already an
audit event, and the audit log covers all writers (API, worker, AI worker), so deriving signals from
it needs no new instrumentation in each service and cannot drift from what is audited.

**Consequences.** Signals lag by up to 30 seconds plus the scrape interval. Identifying who or what
triggered an alert is an auditor's task in the audit log, which keeps it audited. Not built:
OpenTelemetry tracing (the request ID is the correlation key), log aggregation, Alertmanager.
Prometheus and Grafana were configured but not run in Phase 8 (images not pulled); a unit test checks
that the rules and dashboard use only exported metrics.

---

## ADR-039: Encrypted logical backups with a manifest and an automated restore test

**Status:** Accepted for DEMO (Phase 8); production uses managed backups

**Decision.** `scripts/backup_restore.py` takes a `pg_dump` (custom format) in one exported snapshot,
encrypts it inside the database container with GnuPG symmetric AES-256 (integrity-protected,
S2K SHA-512) using `CRIMEMAP_BACKUP_PASSPHRASE`, and writes a manifest read in the same snapshot:
every table's row count, the migration version, the audit-chain check, row-level security and policy
counts, and the file's SHA-256. The restore test checks the hash, restores into a scratch database,
compares everything with the manifest and checks row-level security for the application role.

**Why.** It proves a backup is usable (spec: "restore test succeeds"), detects tampering before and
during decryption, and never leaves a plaintext dump on the host.

**Consequences.** No point-in-time recovery: data after the last backup is lost (runbook §5). The
superuser takes the dump (row-level security is forced on the owner); production uses a dedicated
backup role and the platform's managed, encrypted backups with WAL archiving.

