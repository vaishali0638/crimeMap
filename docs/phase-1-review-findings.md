# Phase 1 Independent Review — Findings Register

**Date:** 2026-10-12. Three independent, read-only reviews (backend API/worker, web BFF, infrastructure/CI)
were run during the Phase 1 audit. The audit was paused at the owner's request to start Phase 2, so
most findings are **open**. Status values: **open** · **fixed** (with where) · **n/a** (verified not
applicable). Severities are the reviewers' own and have not all been re-verified.

## Web / BFF (`apps/web`)

| ID | Sev | Finding | Status |
|---|---|---|---|
| W1 | High | `loadSession` rewrites the whole sealed session to touch `lastSeenAt` outside the lock, so a concurrent request can overwrite freshly rotated tokens; next refresh presents a revoked token and the user is signed out | open |
| W2 | High | Refresh lock TTL (10 s) shorter than openid-client's 30 s timeout; unlock `DEL` does not check ownership | open |
| W3 | Med | Lock wait (5 s) shorter than lock TTL; lock timeout treated as refresh failure → spurious sign-out | open |
| W4 | Med | Any refresh error (network, 5xx) destroys the session; only `invalid_grant` should | open |
| W5 | Med | `refresh_token ?? null` drops the old refresh token when the IdP does not rotate it | open |
| W6 | Med | No Redis connect/command timeouts or offline-queue limit — requests hang when Redis is down | open |
| W7 | Med | Logout skips IdP end-session when the BFF session is already gone, yet says the IdP session ended | open |
| W8 | Med | Proxy reads the whole request body before enforcing the 64 KiB limit | open |
| W9 | Med | Step-up (`?reauth=1`) not verified in the callback (`maxAge`/`auth_time`) | open |
| W10 | Low-Med | Upstream/Redis errors in BFF routes become bare 500s without logging or BFF headers | open |
| W11 | Low | Cross-site POST to logout clears cookies without CSRF check when no session cookie is sent | open |
| W12 | Low | Previous session not destroyed/revoked on re-login | open |
| W13 | Low | Callback failures not logged; flow cookie not cleared on error | open |
| W14 | Med | Account menu: roles and MFA status not reachable by keyboard/screen reader (menu content not menu items) | open |
| W15 | Med | No HSTS on https deployments | open |
| W16 | Low-Med | Cookies lack `__Host-` prefix in production | open |
| W17 | Med | Authentication enforced only in the `(app)` layout; pages that fetch data must re-check | open (relevant from Phase 3) |
| W18 | Low | BFF idle/absolute limits rely on matching Keycloak SSO limits; add a config test | open |
| W19 | Low | Possible login loop if `/auth/me` keeps returning 401 | open |
| W20 | Low | `login-events` block is a denylist entry; prefer allowlist or BFF-only credential | open |
| W21 | Low | Body-less DELETE/POST rejected with 415 | open |
| W22 | Low | Client IP / user agent not forwarded to the API audit log | open |
| W23 | Low | AES-GCM without AAD; one key for sessions and flow cookies | open |
| W24 | Low | CSP style-src fragility; static `/_not-found`; prefetch requests bypass proxy | open |
| W25 | Low | CSRF cookie unused (pattern is synchronizer token); helper modules lack `server-only` | open |

## Backend API and worker (`services/api`, `services/worker`)

| ID | Sev | Finding | Status |
|---|---|---|---|
| B1 | Med-High | IdP importer holds the audit-chain lock for a whole batch; overlapping runs can deadlock | open (Phase 2 ingest designed to avoid the same pattern) |
| B2 | Med | `TRUSTED_PROXIES` covers all private ranges → client IP spoofing for rate limits and audit; `--proxy-headers` double-processes XFF | open |
| B3 | Med | Audit DEFAULT partition can block future monthly partition creation | open |
| B4 | Med | `ensure_month_partition` bounds depend on session TimeZone | open |
| B5 | Med | Attempted username stored as unsalted truncated SHA-256 (dictionary-attackable) | open |
| B6 | Med | `http://` check is case-sensitive (`HTTP://` bypasses the PRODUCTION https rule) | open |
| B7 | Med | JWKS parse errors (`InvalidKeyError`, non-dict body) not caught; empty key set replaces cache; no periodic refresh | open |
| B8 | Med | `environment` defaults to DEMO everywhere; a missing variable in production disables PRODUCTION guards | open |
| B9 | Med | DEMO_USER MFA exemption not restricted to DEMO/SANDBOX | open |
| B10 | Low-Med | Business-rule failures inside admin routes (404/422/409) are not audited | open |
| B11 | Low | Concurrent grant revocation double-audits | open |
| B12 | Low | Near-future `valid_to` can fail the DB CHECK → 500 | open |
| B13 | Low | `account_not_active` failures audited without actor | open |
| B14 | Low | Session leak if `UnitOfWork.__aenter__` fails | open |
| B15 | Low | Oversized `exp`/`iat` or `before_seq` → 500 instead of 4xx | open |
| B16 | Med | Chain verification will break under retention (partition drops) and scans the whole table in-request | open |
| B17 | Med | Policy engine treats "no target" as in scope; `aggregate_only`, classification ceiling, beat/record-type/export scopes not enforced; RLS settings names differ from ADR-002 | open (Phase 2 routes pass explicit scope) |
| B18 | Low | No default `REVOKE EXECUTE` for future functions; worker may create any month partition; passwords visible as `psql -v` args during init | open |
| B19 | Low | `/auth/login-events` replayable by any valid token | open |
| B20 | Low | Importer claims non-security events; content-hash dedupe merges identical events; `occurred_at` = import time | open |
| B21 | Low | Owner can still bypass audit protections (partition TRUNCATE/DETACH, DISABLE TRIGGER) — recorded residual risk | open (accepted residual) |
| B22 | Low | MFA depends on Keycloak `amr`/`auth_time` mappers | verified live in Phase 1 (`amr = ["pwd","otp"]`) |

## Infrastructure, CI, configuration

| ID | Sev | Finding | Status |
|---|---|---|---|
| I1 | High | Postgres healthcheck (Unix socket) can pass during first-run init → `migrate` fails on a fresh volume | open |
| I2 | Med | `pnpm dev:web` does not load the repo-root `.env`; README still mentions port 3000 | open |
| I3 | Med | Compose secrets use `${VAR}` not `${VAR:?}` — missing secret silently empty (Redis without auth) | open |
| I4 | Med | Re-running bootstrap after deleting `.env` desynchronises secrets from existing volumes | open |
| I5 | Med | Keycloak healthcheck `grep -q UP` can match a 503 body | open |
| I6 | Med | ruff/mypy unpinned in CI | open |
| I7 | Med | Redocly run via unlocked `dlx` | open |
| I8 | Med | `TRUSTED_PROXIES` too broad (same as B2) | open |
| I9 | Low | gitleaks staging via `xargs tar` can be silently incomplete | open |
| I10 | Low | `check_docs.py` and worker format check not in CI | open |
| I11 | Low | Bootstrap hard-codes the Redis host port in `CRIMEMAP_REDIS_URL` | open |
| I12 | Low | scheduler lacks `/tmp` tmpfs; migrate lacks hardening | open |
| I13 | Med | `.gitignore` gaps (Terraform state/vars, kubeconfig, keys, `.npmrc`, `.claude/`) | open |
| I14 | Med | Keycloak deprecated `UPDATE_TOTP`/`REMOVE_TOTP`; lockout and client-login-error events not enabled | open |
| I15 | Med | No Python lock file; Dockerfile reinstalls deps on every source change; base images not digest-pinned | open |
| I16 | Low-Med | Secrets visible in process listings / `docker inspect` | open |
| I17 | Low | SeaweedFS ran as root with no healthcheck; internal ports reachable on the Compose network | partly fixed (non-root uid 1000, read-only, caps dropped, healthcheck added on 2026-10-12); internal ports open |
| I18 | Low | trivy DB re-downloaded per CI scan; base-image HIGH fixes may flake | open |
| I19 | Low | SBOM lacks Python dependencies and images | open |
| I20 | Low | Pinned action majors run on deprecated Node 20 | open |
| I21 | Low | `migrate` always seeds demo identities outside PRODUCTION | open |
| I22 | Low | Refresh rotation race (same as W1–W3); no backchannel logout | open |
| I23 | Low | `.dockerignore` matches only the root `.env` | open |
| I24 | Low | No healthchecks for api/worker/scheduler containers | open |
| I25 | Low | CI step name overstates coverage | open |

## Other open items from the paused audit

- `/metrics` endpoint promised for Phase 1 in docs/api.md is not implemented.
- Object store: S3 credentials were rotated after accidental exposure in local tool output; the
  `crimemap-reports` bucket has not been created and the app identity is not yet scoped to it.

## Found or changed during Phase 2

- **Fixed:** user provisioning set `is_demo = true` in every environment (see phase-2-report §3 #4).
- **Fixed:** CI pinned `@redocly/cli@2.62.1`, which is inside the 7-day release-age window (now 2.57.0).
- **Fixed:** the migration round-trip test wiped the shared test database (now uses a scratch DB).
- **New, open:** the first sign-in after TOTP enrolment has `amr = ["pwd"]`, so MFA-gated access
  needs a second sign-in. The UI now explains this.
- B1 still open for the IdP importer. The Phase 2 ingest pipeline takes the audit-chain lock only
  for its final insert (ADR-027).
- **Closed in the Phase 2 audit:** B17 (empty-scope grants passed non-territorial checks) — the policy
  engine now fails closed (docs/phase-2-audit.md, F4).
