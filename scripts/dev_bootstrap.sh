#!/usr/bin/env bash
# Create local development secrets. Safe to re-run: never overwrites an existing .env.
#   - .env                           all service secrets (git-ignored, mode 600)
#   - secrets/demo-credentials.txt   demo usernames + shared demo password (git-ignored, mode 600)
#   - secrets/seaweedfs-s3.json      object-store credentials (git-ignored, mode 600)
# Secrets are generated locally and never printed.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

if [[ -f .env ]]; then
  echo ".env already exists — leaving it unchanged."
else
  python3 - <<'PY'
import base64, re, secrets, string

def token(n: int = 24) -> str:
    return secrets.token_urlsafe(n)

def demo_password() -> str:
    # Satisfies the realm policy: length >= 14, upper, lower, digit.
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(20))
        if re.search(r"[A-Z]", pw) and re.search(r"[a-z]", pw) and re.search(r"\d", pw):
            return pw

values = {
    "POSTGRES_SUPERUSER_PASSWORD": token(),
    "CRIMEMAP_MIGRATOR_PASSWORD": token(),
    "CRIMEMAP_APP_PASSWORD": token(),
    "CRIMEMAP_WORKER_PASSWORD": token(),
    "CRIMEMAP_AI_PASSWORD": token(),
    "REDIS_PASSWORD": token(),
    "KEYCLOAK_ADMIN_PASSWORD": token(),
    "CRIMEMAP_KC_WEB_CLIENT_SECRET": token(32),
    "CRIMEMAP_KC_AUDIT_SYNC_SECRET": token(32),
    "CRIMEMAP_DEMO_PASSWORD": demo_password(),
    "CRIMEMAP_S3_ACCESS_KEY": "crimemap" + secrets.token_hex(6),
    "CRIMEMAP_S3_SECRET_KEY": token(30),
    "CRIMEMAP_SESSION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    "CRIMEMAP_DEMO_SIGNING_KEY": token(32),
    "CRIMEMAP_METRICS_TOKEN": token(32),
    "CRIMEMAP_BACKUP_PASSPHRASE": token(32),
}
values["CRIMEMAP_REDIS_URL"] = f"redis://:{values['REDIS_PASSWORD']}@localhost:56379/2"
out = []
for line in open(".env.example", encoding="utf-8"):
    key = line.split("=", 1)[0].strip()
    out.append(f"{key}={values[key]}\n" if key in values else line)
open(".env", "w", encoding="utf-8").writelines(out)
PY
  chmod 600 .env
  echo "Created .env with generated secrets."
fi

# Keys added in later phases: append to an existing .env without printing them.
for key in CRIMEMAP_DEMO_SIGNING_KEY CRIMEMAP_AI_PASSWORD CRIMEMAP_METRICS_TOKEN CRIMEMAP_BACKUP_PASSPHRASE; do
  if ! grep -q "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
    echo "Added ${key} to .env."
  fi
done

mkdir -p secrets
chmod 700 secrets
set -a
# shellcheck disable=SC1091
source .env
set +a

cat > secrets/demo-credentials.txt <<EOF
CRIMEMAP DEMO identities (synthetic data only). Keycloak: http://localhost:8080/realms/crimemap-demo
Every demo user shares this password and must enrol an authenticator app (TOTP) at first sign-in.

password: ${CRIMEMAP_DEMO_PASSWORD}
users: demo.admin demo.steward demo.analyst demo.officer demo.sho demo.supervisor demo.leadership demo.auditor demo.viewer
EOF
chmod 600 secrets/demo-credentials.txt

cat > secrets/seaweedfs-s3.json <<EOF
{"identities": [{"name": "crimemap",
  "credentials": [{"accessKey": "${CRIMEMAP_S3_ACCESS_KEY}", "secretKey": "${CRIMEMAP_S3_SECRET_KEY}"}],
  "actions": ["Read", "Write", "List", "Tagging"]}]}
EOF
chmod 600 secrets/seaweedfs-s3.json

# Prometheus reads the /metrics bearer token from a file (observability profile). Compose mounts
# file secrets as-is (it ignores uid/mode outside swarm), and Prometheus (uid 65534) and Grafana
# (uid 472) must read them, so these two files are 0644. They stay private on the host because
# secrets/ itself is 0700 (Phase 8 audit).
printf '%s' "${CRIMEMAP_METRICS_TOKEN}" > secrets/metrics-token
chmod 644 secrets/metrics-token
if [[ ! -f secrets/grafana-admin-password ]]; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(24), end="")' > secrets/grafana-admin-password
fi
chmod 644 secrets/grafana-admin-password
echo "Wrote secrets/demo-credentials.txt, secrets/seaweedfs-s3.json and secrets/metrics-token (not printed)."
