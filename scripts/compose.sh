#!/usr/bin/env bash
# Run Docker Compose for the local stack with the repo's .env, whichever Compose is installed
# ("docker compose" plugin or the standalone "docker-compose").
#   scripts/compose.sh up -d                      # postgres, redis, keycloak
#   scripts/compose.sh --profile app up -d        # + migrate, api, worker, scheduler
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Run scripts/dev_bootstrap.sh first." >&2; exit 1; }
if docker compose version >/dev/null 2>&1; then
  exec docker compose --env-file .env -f infrastructure/docker/compose.yaml "$@"
else
  exec docker-compose --env-file .env -f infrastructure/docker/compose.yaml "$@"
fi
