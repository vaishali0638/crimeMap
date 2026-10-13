# Phase 1 Delivery Report — Project Scaffolding and Secure Identity

**Date:** 2026-10-11 to 2026-10-12 · **Environment:** DEMO / SANDBOX (synthetic data only) · **Go/no-go:** see §9.
Nothing has been committed or pushed; CI has not run on GitHub (the repository has no remote).

## 1. Specification acceptance criteria

| Criterion (spec Phase 1) | Result | Evidence |
|---|---|---|
| MFA-ready login flow works in development | **Met** | Browser test against Keycloak 26.8.0: password → mandatory TOTP enrolment → PKCE code exchange → BFF session; a later sign-in with password + TOTP produced `amr = ["pwd","otp"]` and "MFA ✓ verified" in the UI (audit seq 5). Sign-out ends both the CRIMEMAP and Keycloak sessions. |
| Unauthorised API calls are denied | **Met** | 401 for missing/forged/expired/misdirected tokens; 403 for missing permission, missing MFA, stale login; 404 for path traversal at the BFF; CSRF-less POST rejected — integration tests and live browser probes. |
| Audit logs record login, failed login, authorised resource view | **Met** | `LOGIN` from the BFF callback (live); `LOGIN_FAILED` imported from Keycloak by the worker (live, seq 12, username stored only as a hash); `RECORD_VIEW` / `AUDIT_VIEW` (integration tests); `AUTHZ_DENIED` live (seq 14). Hash chain verifies. |
| CI passes lint, type-check, tests, SAST/dependency checks | **Met locally; not run on GitHub** | Every CI step was run locally with the same tools and versions and passes (§4). The GitHub Actions workflow itself has not run because the repository has no remote. |

## 2. What was built

| Area | Delivered |
|---|---|
| Monorepo | `apps/web`, `services/api`, `services/worker`, `packages/{ui,types,config}`, `infrastructure/{docker,terraform-or-opentofu,kubernetes}`, `scripts/` |
| Local environment | Docker Compose (PostgreSQL 16 + PostGIS 3.6, Redis 7.4, Keycloak 26.8, SeaweedFS S3, API, worker, scheduler); localhost-only ports; memory limits; `scripts/dev_bootstrap.sh` generates all secrets into git-ignored files |
| Identity | Keycloak realm `crimemap-demo` (one fictional user per role, mandatory TOTP, PKCE confidential client, rotating refresh tokens, brute-force lockout, event logging); BFF with encrypted Redis sessions, CSRF double-submit + Origin check, token refresh under a per-session lock |
| Authorisation | Policy engine (role + territorial scope + MFA + purpose + fresh login), matrix identical to the documentation (enforced by test), startup check that every route declares a requirement, separation of duties for role changes |
| Audit | Append-only, monthly-partitioned, hash-chained `audit.audit_event`; column-level grants prevent backdating; denials and errors audited in independent transactions; IdP event import (exactly once); verification function and job |
| API | FastAPI: health/ready, `auth/me`, `auth/login-events`, users, role grants (create/revoke), roles, permissions, audit events, audit verification; RFC 9457 errors; security headers; rate limits; OpenAPI 3.1 with per-operation permissions |
| Web | Next.js 16 app shell with safeguard strip, navigation, account menu, honest phase placeholders; per-request CSP nonces; generated API types |
| CI | `.github/workflows/ci.yml` (ruff, mypy, bandit, pip-audit, pytest with PostGIS, OpenAPI lint + type drift, ESLint, tsc, Vitest, build, pnpm audit, gitleaks, semgrep, trivy, SBOM, image scans) |

## 3. Defects found and fixed during Phase 1

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | Route-permission check inspected **zero** routes on FastAPI 0.143 (would pass vacuously) | Smoke test printed nothing | Public `iter_route_contexts`; fail if no routes; pinned route table test (ADR-023) |
| 2 | Keycloak `amr` always empty (`default.reference.maxAge` defaults to 0) | Live login audited `amr: []` | maxAge = SSO session lifetime; realm test (ADR-004) |
| 3 | Sign-out form unmounted by the menu before submit | Browser: no request sent | Real submit button; menu stays open on select |
| 4 | `Referrer-Policy: no-referrer` made browsers send `Origin: null`, failing CSRF check | Browser: 403 on sign-out | `same-origin` for web pages + test (ADR-025) |
| 5 | CSP `form-action 'self'` blocked the redirect to the IdP end-session endpoint | Browser: aborted navigation, reproduced with a test form | Allow the IdP origin; CSP builder + tests (ADR-025) |
| 6 | New grants briefly unusable when the DB clock ran ahead of the API host | 18 integration tests failed | Grant validity decided by the DB clock + tests (ADR-024) |
| 7 | API `environment` typed as free text; UI expected an enum | Generated contract types | Literal type in the API schema |
| 8 | Keycloak data volume root-owned | Keycloak failed to start | Mount at the image-owned path |
| 9 | Host ports 5432 / 6379 / 3000 already in use by other software | Connection reached Homebrew PostgreSQL | Stack moved to 55432 / 56379 / 3100 (configurable) |
| 10 | Local gitleaks scanned an empty directory (Colima shares only `$HOME`) | "scanned ~0 bytes" | Stage inside the repo; script fails if the scanner sees no files |
| 11 | CSRF rejections never reached any audit trail | Review of live denial path | Structured BFF security events |
| 12 | `upgrade-insecure-requests` on plain-http demo | Code review before start | Only when served over https |
| 13 | PostgreSQL container started as root (trivy DS-0002, HIGH) | trivy misconfiguration scan | `USER postgres`; init scripts owned by postgres |
| 14 | 25 HIGH/1 CRITICAL CVEs in upstream `gosu` binary (Go stdlib) | trivy image scan | `gosu` removed (image never runs as root) — image now 0 HIGH/CRITICAL |
| 15 | 20 semgrep findings: mutable action tags, GCM without `authTagLength`, SQL built with f-strings, missing pnpm supply-chain settings | semgrep | Actions pinned to SHAs; `authTagLength`; fully literal SQL (no `nosec` left); pnpm policies — now 0 findings with `--error` |
| 16 | ~60 lockfile packages younger than 7 days | pnpm `minimumReleaseAge` | Five direct dependencies moved to the newest compliant release (Next.js 16.4.0 → 16.3.8); re-verified build, tests, and browser flow |
| 17 | Two `trustPolicy` downgrades (`eslint-import-resolver-typescript@3.10.1`, `semver@6.3.1`) | pnpm `trustPolicy: no-downgrade` | Each investigated: old-major maintenance releases, no same-major release ever had provenance; exact-version exclusions with evidence in `pnpm-workspace.yaml` |
| 18 | Web server listened on all interfaces (reachable from the LAN) | Next.js startup log | Bound to 127.0.0.1; LAN connection refused |

## 4. Verification (run locally, 2026-10-11)

| Check | Result |
|---|---|
| Python unit + integration + worker tests (pytest, real PostGIS via testcontainers) | **185 passed** |
| Mutation checks (10 security defects planted one at a time: audit UPDATE/DELETE grant, hash check skipped, backdating, permission removed, denial not audited, SoD removed, admin MFA exemption, fresh-login removed, admin sees audit content, audit not flushed) | **10/10 caught** |
| ruff lint + format, mypy `--strict` (API 25 files, worker 6 files) | Pass |
| Web: Vitest (56), `tsc --noEmit` (strict), ESLint (`--max-warnings 0`, incl. jsx-a11y), `next build` | Pass |
| OpenAPI: Redocly lint (recommended ruleset, `operation-4xx-response` skipped), generated-types drift check (and tamper detection) | Pass |
| bandit, pip-audit (third-party packages), pnpm audit `--prod` (high) | Pass |
| gitleaks (145 committable files, ~493 KB; scanner verified to see the files) | No leaks |
| semgrep (default, Python, TypeScript rulesets, `--error`; 121 files) | **0 findings** |
| trivy filesystem + IaC (pnpm lockfile, Dockerfiles) | **0 HIGH/CRITICAL** |
| trivy images (api, worker, postgres) | **0 HIGH/CRITICAL** (after defects 13–14) |
| pnpm `--frozen-lockfile` install under supply-chain policies | Pass |
| Container images build + Compose `app` profile (migrate → API, worker, scheduler) | Pass: API ready; worker ran audit verification and IdP import; containers non-root, read-only, all capabilities dropped |
| Browser journey against the containerised API on the final dependency versions | Pass (password + TOTP, `mfa: true`, 403 on admin endpoint, audited) |
| Documentation checks (`scripts/check_docs.py`) | Pass |

## 5. Security status

Implemented and tested: OIDC + PKCE, mandatory TOTP, MFA enforcement for all privileged and case-data
roles, deny-by-default policy engine, least-privilege database roles, immutable hash-chained audit log,
CSRF protection, strict CSP with nonces, rate limiting (including authentication failures), problem
responses without internals, secrets only in git-ignored files. Not yet done: TLS (local http only),
encryption at rest beyond the host disk, alert routing, retention jobs, independent security audit.

## 6. Commands

See README "Local setup" and "Commands". Integration tests on this machine need
`DOCKER_HOST=unix://$HOME/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock TESTCONTAINERS_RYUK_DISABLED=true`.

## 7. Known limitations

- The first sign-in after TOTP enrolment carries `amr = ["pwd"]`, so case-data access is denied until
  the user signs in again with the authenticator (fails safe; UI prompt planned for Phase 3).
- Client IPs recorded for Keycloak events are the Docker network gateway in the local setup.
- Station-scoped demo users (officer, SHO) have no stations until Phase 2 seeds fictional stations.
- OpenAPI does not yet document 401/403/429 responses per operation.
- No automated browser (Playwright) tests yet; the login journey was verified manually in Phase 1.
- Next.js's built-in 404 page is static, so it carries no CSP nonce.
- Scanner container images are pinned by tag, not digest (GitHub Actions are pinned by SHA).
- Worker logs use Dramatiq's plain-text format, not the API's JSON format.
- pytest shows one deprecation warning from Starlette's test client (httpx); harmless today.
- `eslint@9.39.5` is reported deprecated upstream; staying on 9.x until the bundled Next.js ESLint plugins support ESLint 10.
- Proposed ADRs (004, 006, 012, 013, 015, 017, 018, 020) still await owner confirmation.

## 8. Next phase

Phase 2 — data foundation: PostGIS schema for boundaries and incidents with row-level security,
synthetic boundary and incident generators for the four regions, ingestion staging with idempotency
and lineage, data-quality rules, and the demo adapter.

## 9. Go / no-go

**GO for Phase 2**, in DEMO/SANDBOX mode. All four Phase 1 acceptance criteria are met with recorded
evidence, with one qualification: CI is proven locally but has not yet run on GitHub. **Not**
production-ready: no TLS, no independent security audit, no hosting decision, no legal approvals, and
the open items in §7 remain.
