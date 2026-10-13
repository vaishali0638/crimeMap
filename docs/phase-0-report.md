# Phase 0 Delivery Report — Discovery, Threat Model, and Foundations

**Date:** 2026-10-11 · **Environment:** DEMO / SANDBOX (design only) · **Go/no-go:** **GO for Phase 1**,
subject to owner confirmation of the Proposed ADRs listed in §2.

## 1. Files created

| File | Purpose |
|---|---|
| `.gitignore` | Keeps secrets, env files, build output, and local data out of git |
| `README.md` | Project status, document index, planned layout, prerequisites |
| `docs/decision-log.md` | ADR-001 … ADR-020 |
| `docs/architecture.md` | Components, request lifecycle, ingestion flow, state machines, risks |
| `docs/threat-model.md` | Assets, actors, trust boundaries, STRIDE table, abuse cases, residual risks |
| `docs/data-classification-and-access.md` | Classification levels, scope dimensions, permission catalogue, role matrix, masking, step-up |
| `docs/data-dictionary.md` | Logical model for all 18 specified entities plus supporting tables |
| `docs/demo-data.md` | Synthetic data policy and generator design |
| `docs/information-architecture.md` | Site map, wireframes, design tokens, accessibility commitments |
| `docs/integration-contracts.md` | Approval prerequisites and adapter contract for future sources |
| `docs/api.md` | API conventions, response envelope, error catalogue, endpoint groups |
| `docs/acceptance-test-plan.md` | Definition of Done, test layers, screen/E2E/security/performance tests |
| `docs/requirements-traceability.md` | Requirement → design → phase → verification |
| `docs/phase-0-report.md` | This report |
| `docs/phase-0-audit.md` | Audit of all Phase 0 files against the specification: 24 findings, all fixed |

A dedicated git repository was initialised at `~/Documents/crimemap_claude` (ADR-016). Nothing has
been committed.

## 2. Decisions needing owner confirmation (defaults applied)

Role codes now match the specification exactly (see data-classification §4).

| ADR | Default applied |
|---|---|
| 004 | Keycloak dev realm; mock OIDC provider only as a fallback for 8 GB RAM |
| 006 | Self-hosted PMTiles basemap |
| 012 | Small-cell suppression threshold k = 3 for sensitive categories |
| 013 | Synthetic region outlines; fictional stations and beats |
| 015 | GitHub Actions + OpenTofu |
| 017 | Map filter p95 < 500 ms at 200,000 records (fills `[TARGET_MS]`, `[TARGET_RECORD_COUNT]`) |
| 018 | Compose profiles; observability stack off by default |
| 020 | 250 m precision reduction for roles without exact-location permission |

Open question: **hackathon deadline and team size**, which decide how deep each phase goes.

## 3. Commands run

Discovery only: file listings, ZIP listings (`unzip -l`, `unzip -p … SKILL.md`), toolchain version
checks, `git init -b main`. No application commands — there is no application yet.

## 4. Tests run

No application tests exist (no code in Phase 0). Documentation checks, run after the audit fixes on
2026-10-11:

| Check | Result |
|---|---|
| Relative links between documents resolve | Pass (14 files) |
| Code fences balanced; every table row has its header's cell count | Pass |
| Every ADR reference points to an existing ADR | Pass |
| Wireframe boxes: every row matches its box width | Pass (0 misaligned; 33 before fixes) |
| Cross-document consistency: no stale role/table/RFC names; permission catalogue ⇔ role matrix; all 9 specification roles; all 7 named source systems in the source table; 7 Trends boards, 7 Analytics sections, 6 queues; all 18 specification entities; record types valid | Pass |
| UTM zones (Mahe 43N, others 44N) recomputed from longitude | Pass |
| Design-token contrast pairs meet WCAG AA as documented | Pass (failed before fixes, see audit F-05) |
| Secret patterns (keys, tokens, private keys) | None found |
| Personal-data patterns (12-digit IDs, PAN, Indian mobile numbers, emails) | None found |
| `.gitignore`: `.env*` ignored, `.env.example` tracked, key files ignored | Pass |
| Checker self-test: errors planted in a scratch copy (broken link, misaligned row, stale table name, unknown permission, unknown record type, missing source row) | All detected |

Not run (blocked): Mermaid rendering and markdownlint — tools not installed (see audit §3).

## 5. Security checks

Design-level threat model completed (`threat-model.md`). No SAST/SCA/secret scanners were run:
there is no code yet, and semgrep, gitleaks, and trivy are not installed locally (they will run as
containers and in CI from Phase 1).

## 6. Screens / features completed

None. Wireframes only (`information-architecture.md`).

## 7. Known limitations, assumptions, and blocked external dependencies

- No official boundaries, denominators, or source-system access; all are synthetic or stubbed.
- No legal review, data-sharing agreement, security audit, or production hosting decision.
- `analytics-methods.md`, `ai-governance.md`, `runbook.md`, `incident-response.md`, and
  `accessibility.md` are scheduled for later phases.
- Package versions are not yet pinned; current stable releases will be checked at scaffold time.
- Skills: no geospatial skill is installed; `fastapi-expert`/`python-pro` exist on disk but are not
  installed as a plugin; `test-master` is available as a ZIP only.

## 8. Phase 1 plan — project scaffolding and secure identity

Prerequisite: Docker Desktop running.

1. Monorepo skeleton (`apps/web`, `services/api`, `services/worker`, `packages/*`, `infrastructure/*`,
   `scripts/`), pnpm workspace, Python project with pinned dependencies, `.env.example` with
   placeholders only.
2. Docker Compose with profiles: PostGIS, Redis, MinIO, Keycloak (realm import with one demo user per
   role, TOTP for case roles), API, worker.
3. FastAPI baseline: settings, request-ID middleware, security headers, rate limiting, JWT validation,
   RFC 9457 problem-details errors, `/health`, `/ready`, `/auth/me`.
4. Policy engine with the permission catalogue and role matrix from
   `data-classification-and-access.md`; startup check that every route declares a permission.
5. Database roles (`crimemap_migrator`, `crimemap_app`, `crimemap_worker`), first Alembic migration
   (identity tables, audit schema with insert-only grants and hash chain).
6. Next.js app shell with BFF login (OIDC code + PKCE), session cookie, CSRF, safeguard strip.
7. Tests: authentication failures, authorisation matrix, audit events for login / failed login /
   resource view, audit immutability.
8. GitHub Actions CI: lint, type-check, tests, semgrep, bandit, gitleaks, pip-audit, pnpm audit,
   trivy, SBOM, migration check, OpenAPI lint.

Phase 1 acceptance (spec): MFA-ready login works in development; unauthorised API calls are denied;
audit logs record login, failed login, and authorised resource view; CI passes.
