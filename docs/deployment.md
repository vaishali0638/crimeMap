# CRIMEMAP Deployment and Environment Promotion

**Environment:** DEMO, synthetic data only. Not a production-readiness claim. Only the local Docker
Compose deployment exists and has been run. Kubernetes manifests, a hosting platform and a
production pipeline are **not built** (`infrastructure/kubernetes/README.md`); the production
sections below are the policy they must meet. Operations: [runbook.md](runbook.md).

## 1. Local deployment (built, verified)

Prerequisites: Docker (Colima on macOS with 2 vCPU / 4 GB is enough), Node 22+ with pnpm 11, Python
3.12.

```bash
scripts/dev_bootstrap.sh
```

This creates `.env` and `secrets/` with generated secrets (never printed; re-running adds only
missing keys). Then:

```bash
scripts/compose.sh --profile app build migrate worker
```

```bash
scripts/compose.sh --profile app up -d
```

`migrate` runs the Alembic migrations as `crimemap_migrator` before the API starts. Demo data:
`scripts/compose.sh --profile app --profile demo-data run --rm demo-data` (DEMO only). The web app runs on the
host: `pnpm --filter @crimemap/web build`, then `pnpm --filter @crimemap/web start` with `.env`
loaded (port 3100). Optional profiles: `storage` (object store), `observability` (Prometheus and
Grafana; not run in Phase 8).

After a change: rebuild the images, run `scripts/compose.sh --profile app run --rm migrate`, then
`scripts/compose.sh --profile app up -d --force-recreate --no-deps api worker scheduler ai-worker`.

## 2. Environments

The code recognises three values of `CRIMEMAP_ENVIRONMENT`.

| | DEMO | SANDBOX | PRODUCTION |
|---|---|---|---|
| Purpose | Demonstration, development, hackathon | Integration and security testing, user acceptance, penetration tests | Operational use |
| Data | Synthetic only, labelled "DEMO / SYNTHETIC DATA" | Synthetic, or approved test extracts | Approved source data |
| Demo data and demo sources | Allowed | Allowed | **Refused**: the API will not start if demo records exist; demo sources are not ingested |
| Identity | Local Keycloak over http allowed (`CRIMEMAP_ALLOW_INSECURE_OIDC`) | https identity provider | https only; insecure OIDC refused |
| OpenAPI document | Served | Served | Refused |
| HSTS | Off | Off | On |
| Secrets | `.env` / `secrets/` from the bootstrap | Secrets manager, separate from production | Secrets manager; separate keys per environment |
| Integrations | None (government systems are stubs that refuse to run) | Only with written approval | Only with approval, contracts and credentials ([integration-contracts.md](integration-contracts.md)) |

Keys, passwords and signing keys are never shared between environments, and data never moves from
PRODUCTION to a lower environment.

## 3. Promotion policy

A change moves DEMO → SANDBOX → PRODUCTION only as the same, reviewed commit and the same images
(built once, identified by digest), with these gates.

| Gate | DEMO → SANDBOX | SANDBOX → PRODUCTION |
|---|---|---|
| Code review | One reviewer who is not the author | Two reviewers, one from security for changes to authorisation, audit, AI or data access |
| CI (`.github/workflows/ci.yml`) | lint, types, unit and integration tests, OpenAPI drift, web build, audits | same, green on the exact commit |
| Security scans (`scripts/run_security_checks.sh`) | all pass, or exceptions with a risk acceptance | all pass; no unaccepted critical or high finding |
| End-to-end and accessibility (`pnpm --filter @crimemap/web e2e`) | run against DEMO | run against SANDBOX; axe clean |
| Performance (ADR-017) | measured | targets met on the production-sized SANDBOX, or an owner's acceptance |
| Migrations | applied to a SANDBOX copy and rolled back once | backup and restore test immediately before; migration reviewed for locks on large tables |
| AI | model cards current | rollout gates for any newly enabled model ([ai-governance.md](ai-governance.md) §4) |
| Approval | technical lead | system owner and security officer; change record with rollback plan |

Emergency fixes may skip the SANDBOX soak time but not review, CI, scans or the owner's approval;
they are reviewed afterwards.

## 4. Production requirements (not built)

- An approved hosting platform; TLS 1.3 at the edge; the database and object storage encrypted at
  rest with a key-management service; private networking between services.
- Managed PostgreSQL with point-in-time recovery and offsite encrypted backups
  ([runbook.md](runbook.md) §4.4).
- At least two API replicas behind the load balancer; one scheduler; separate worker and AI-worker
  deployments with their own database logins (the AI worker must hold only `crimemap_ai`).
- Secrets from the secrets manager, mounted per service; nothing in images or environment files.
- Prometheus, alert routing and log aggregation operated by the hosting team.
- External penetration test passed ([pentest-remediation-checklist.md](pentest-remediation-checklist.md)).

## 5. Rollback

- **Application:** redeploy the previous image digests. API changes are backwards compatible for one
  release (the web app and API may be one version apart during a rollout).
- **Database:** migrations are forward-only in production. Each migration's `downgrade` is tested in
  CI (the round trip), but a production rollback is done by restoring the pre-deployment backup
  ([runbook.md](runbook.md) §4.3) only when data written since can be re-created; otherwise fix
  forward.
- **AI:** switch the model or all AI off (`/ai`); this needs no deployment.
