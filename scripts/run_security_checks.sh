#!/usr/bin/env bash
# Local security checks — the same tools CI runs. Scanners run as containers, so only Docker is
# required. Exit status is non-zero if any check fails; each result is printed as PASS/FAIL.
#   scripts/run_security_checks.sh            # all checks
#   SKIP_CONTAINER_SCANS=1 scripts/...        # Python/Node checks only (offline-friendly)
set -uo pipefail
cd "$(dirname "$0")/.."
API=services/api
PY="$API/.venv/bin"
failures=0

run() {
  local name="$1"; shift
  echo "── $name"
  if "$@"; then echo "PASS  $name"; else echo "FAIL  $name"; failures=$((failures + 1)); fi
}

run "bandit (Python SAST)" "$PY/bandit" -q -c "$API/pyproject.toml" -r "$API/src" services/worker/src
FROZEN="$(mktemp)"; "$PY/pip" freeze --exclude-editable > "$FROZEN"
run "pip-audit (Python dependencies)" "$PY/pip-audit" -r "$FROZEN" --strict --no-deps --disable-pip
run "pnpm audit (Node dependencies)" pnpm audit --audit-level high --prod

if [[ -z "${SKIP_CONTAINER_SCANS:-}" ]]; then
  # Scan exactly the files git would commit (tracked + untracked, minus .gitignore'd: .env,
  # secrets/, build output). Local secrets live in ignored files by design.
  # The staging copy must be under $HOME: Colima shares only the home directory with its VM, so a
  # /var/folders temp dir appears empty inside the container (a vacuous "no leaks" — Phase 1 finding).
  SCAN_DIR="$PWD/.scan-tmp"; rm -rf "$SCAN_DIR"; mkdir -p "$SCAN_DIR"
  git ls-files -co --exclude-standard -z | xargs -0 tar -cf - | tar -xf - -C "$SCAN_DIR"
  gitleaks_scan() {
    local seen
    seen=$(docker run --rm -v "$SCAN_DIR:/repo:ro" alpine:3 find /repo -type f | wc -l)
    [[ "$seen" -gt 0 ]] || { echo "scanner sees no files (volume not shared)"; return 1; }
    echo "scanner sees $seen files"
    docker run --rm -v "$SCAN_DIR:/repo:ro" zricethezav/gitleaks:v8.30.1 dir /repo --no-banner --redact
  }
  run "gitleaks (secrets in committable files)" gitleaks_scan
  rm -rf "$SCAN_DIR"
  run "semgrep (SAST)" docker run --rm -v "$PWD:/src" semgrep/semgrep:1.180.0 semgrep scan \
    --config p/default --config p/python --config p/typescript --error --metrics off \
    --exclude node_modules --exclude .venv /src
  run "trivy (deps, secrets, IaC)" docker run --rm -v "$PWD:/repo" aquasec/trivy:0.75.0 fs \
    --scanners vuln,secret,misconfig --severity HIGH,CRITICAL --exit-code 1 \
    --skip-dirs /repo/node_modules --skip-dirs /repo/services/api/.venv /repo
fi

echo
if (( failures > 0 )); then echo "$failures check(s) failed"; exit 1; fi
echo "all checks passed"
