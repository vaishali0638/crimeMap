# CRIMEMAP — Geospatial Crime Intelligence, Analytics & Decision-Support System

> **Status: Phase 8 (hardening, performance, accessibility and operations).** See [docs/phase-8-report.md](docs/phase-8-report.md); previously [docs/phase-7-report.md](docs/phase-7-report.md)
> and [docs/ai-governance.md](docs/ai-governance.md). Phase 6: [report](docs/phase-6-report.md),
> [audit](docs/phase-6-audit.md). Earlier phases: [1](docs/phase-1-report.md),
> [2](docs/phase-2-report.md) ([audit](docs/phase-2-audit.md)), [3](docs/phase-3-report.md)
> ([audit](docs/phase-3-audit.md)), [4](docs/phase-4-report.md), [5](docs/phase-5-report.md)
> ([audit](docs/phase-5-audit.md)).
> Environment: **DEMO / SANDBOX** only. All data will be **DEMO / SYNTHETIC DATA**.

CRIMEMAP is an **internal** police decision-support platform designed for the Puducherry Police
hackathon context. It is **not** a public crime map, an FIR-registration system, or an autonomous
policing system. CCTNS or another officially authorised system remains the authoritative case/FIR
record. No government system is integrated, simulated, or accessed.

The authoritative requirements source is `crimemap_master_build_prompt.md` (kept outside this
repository by the project owner).

## Phase 0 deliverables

| Deliverable (spec Phase 0) | Document |
|---|---|
| Phase 0 delivery report / go–no-go | [docs/phase-0-report.md](docs/phase-0-report.md) |
| Phase 0 audit (findings and fixes) | [docs/phase-0-audit.md](docs/phase-0-audit.md) |
| Architecture decision records | [docs/decision-log.md](docs/decision-log.md) |
| Architecture overview and data flow | [docs/architecture.md](docs/architecture.md) |
| Threat model (STRIDE) | [docs/threat-model.md](docs/threat-model.md) |
| Data classification and authorisation matrix | [docs/data-classification-and-access.md](docs/data-classification-and-access.md) |
| Data dictionary | [docs/data-dictionary.md](docs/data-dictionary.md) |
| Demo-data policy | [docs/demo-data.md](docs/demo-data.md) |
| Wireframes and information architecture | [docs/information-architecture.md](docs/information-architecture.md) |
| Integration contracts (future, approved sources only) | [docs/integration-contracts.md](docs/integration-contracts.md) |
| API design outline | [docs/api.md](docs/api.md) |
| Definition of Done and acceptance test plan | [docs/acceptance-test-plan.md](docs/acceptance-test-plan.md) |
| Requirements traceability | [docs/requirements-traceability.md](docs/requirements-traceability.md) |

## Local setup (DEMO mode)

Prerequisites: Colima (or another Docker engine), Python 3.12, Node.js 26 + pnpm 11.

```bash
colima start --cpu 2 --memory 4 --disk 30 --vm-type vz   # once per boot
scripts/dev_bootstrap.sh                                  # generates .env and secrets/ (never printed)
scripts/compose.sh up -d                                  # PostgreSQL+PostGIS, Redis, Keycloak
scripts/compose.sh --profile app up -d                    # migrations, API, worker, scheduler, ai-worker
pnpm install && pnpm dev:web                              # web app on http://localhost:3100
scripts/compose.sh --profile app --profile demo-data run --rm demo-data  # 200k synthetic records (~2 min)
```

Administrative CLIs (`crimemap_api.cli`, `crimemap_worker.ingest_cli`) require the environment to be
set explicitly (`CRIMEMAP_ENVIRONMENT` / `CRIMEMAP_WORKER_ENVIRONMENT` = DEMO, SANDBOX or
PRODUCTION); an unset value is refused, never assumed to be DEMO. Scheduled inbox ingestion is off
by default: set `CRIMEMAP_WORKER_INBOX_DIR` (a writable volume) and `CRIMEMAP_DEMO_SIGNING_KEY` on
the worker to enable it (ADR-030). Source health checks and the daily reconciliation report always
run on the scheduler. The `demo-data` job needs about 1.5 GB of memory; with a 4 GB Colima VM, stop
the scheduler while it runs if memory is tight.

The Home Map shows no basemap by default, so the browser makes no third-party requests (ADR-033). To use an
approved basemap, set `CRIMEMAP_MAP_STYLE_URL` to an https MapLibre style URL, and
`CRIMEMAP_MAP_ASSET_ORIGINS` to the https origins its tiles, glyphs and sprites come from, before
starting the web app. An invalid value turns the basemap off (logged) instead of failing pages.
After a large bulk load, run `ANALYZE core.incident_record, core.incident_location;` so the map
queries get good plans. The migrations do this for their own backfills.

Demo identities (one per role) and the shared demo password are in `secrets/demo-credentials.txt`.
Every demo user must enrol an authenticator app (TOTP) at first sign-in. All data is
**DEMO / SYNTHETIC**; no government system is connected.

## Commands

| Task | Command |
|---|---|
| API unit tests | `cd services/api && .venv/bin/pytest tests/unit ../worker/tests` |
| API integration tests (Docker) | `cd services/api && DOCKER_HOST=unix://$HOME/.colima/default/docker.sock TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock TESTCONTAINERS_RYUK_DISABLED=true .venv/bin/pytest tests/integration` |
| Python lint / types | `ruff check`, `ruff format --check`, `mypy` (in `services/api` and `services/worker`) |
| Web lint / types / tests / build | `pnpm --filter @crimemap/web lint`, `typecheck`, `test`, `build` |
| Security checks | `scripts/run_security_checks.sh` |
| Documentation checks | `python3 scripts/check_docs.py` |
| Map load test (local Compose DB, in-process API) | `cd services/api && set -a && . ../../.env && set +a && .venv/bin/python scripts/bench_map.py` |
| End-to-end, accessibility and visual tests (stack and web app running; installed Chrome) | `set -a && . ./.env && set +a && pnpm --filter @crimemap/web e2e` |
| Encrypted backup / restore test | `services/api/.venv/bin/python scripts/backup_restore.py backup` then `restore-test backups/<file>` (with `.env` loaded) |
| Observability (Prometheus :9090, Grafana :3300) | `scripts/compose.sh --profile app --profile observability up -d` |

Phase 8 added the operating documents: [runbook](docs/runbook.md),
[incident response](docs/incident-response.md), [deployment and promotion](docs/deployment.md),
[accessibility](docs/accessibility.md), [analytics methods](docs/analytics-methods.md),
[user guide](docs/user-guide.md), [admin guide](docs/admin-guide.md),
[security audit checklist](docs/security-audit-checklist.md) and
[penetration-test remediation checklist](docs/pentest-remediation-checklist.md).

## Repository layout

```
apps/web                 Next.js frontend (BFF route handlers)
services/api             FastAPI modular monolith
services/worker          Dramatiq background jobs
packages/ui              accessible design-system components
packages/types           OpenAPI-generated TypeScript types
packages/config          shared lint/format/tsconfig
infrastructure/docker    Compose files and service config
infrastructure/terraform-or-opentofu  infrastructure-as-code (OpenTofu, reference)
infrastructure/kubernetes  manifests (reference)
docs/                    this documentation set
scripts/                 generate_demo_data.py, seed_boundaries.py (wrappers), run_security_checks.sh
```

## Development machine notes (verified 2026-10-11)

- Container runtime is **Colima** with the Homebrew Docker CLI (not Docker Desktop — corrected from
  Phase 0; see ADR-022). `scripts/compose.sh` works with either `docker compose` or `docker-compose`.
- Node.js 26 (`.nvmrc`), pnpm 11, Python 3.12.
- Security scanners (semgrep, gitleaks, trivy, syft) run as containers.
