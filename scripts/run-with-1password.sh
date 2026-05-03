#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run-with-1password.sh — Launch monitoring-stack with secrets from 1Password.
#
# Secrets are resolved in-memory by `op run --env-file` and NEVER touch disk,
# EXCEPT secrets that must be exposed as files for legacy consumers:
#   - K8s kubelet bearer token -> tmpfs file so Prometheus can use
#     bearer_token_file.
#   - pfSense monitoring SSH private key -> tmpfs file so Docker bind mounts
#     can pass it to exporter and WAN guard without a long-lived plaintext key
#     in the repo.
#
# Tmpfs file lifecycle:
#   - Created on "up" commands, persists for the lifetime of the stack.
#   - Removed on "down" commands, ensuring clean teardown.
#   - /dev/shm is tmpfs, so the files never survive a reboot.
#
# Non-secret static config lives in .env.nonsecret (committed).
#
# Usage:
#   ./scripts/run-with-1password.sh up -d
#   ./scripts/run-with-1password.sh --profile mcp --profile nextgen up -d
#   ./scripts/run-with-1password.sh down
#   ./scripts/run-with-1password.sh logs -f grafana
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

# ── 1Password CLI ──────────────────────────────────────────────────────────
OP_BIN="${OP_BIN:-/mnt/pool_fast/opencode/bin/op}"

if [[ ! -x "$OP_BIN" ]]; then
  echo "ERROR: 1Password CLI not found at $OP_BIN"
  echo "Set OP_BIN to the correct path or install the op CLI."
  exit 1
fi

# Service Account token (non-interactive auth for automation)
SA_TOKEN_FILE="${SA_TOKEN_FILE:-/mnt/pool_fast/db/secrets/1password-mcp/token}"
if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" && -f "$SA_TOKEN_FILE" ]]; then
  export OP_SERVICE_ACCOUNT_TOKEN
  OP_SERVICE_ACCOUNT_TOKEN="$(tr -d '\r\n' < "$SA_TOKEN_FILE")"
fi

if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  echo "ERROR: No OP_SERVICE_ACCOUNT_TOKEN set and $SA_TOKEN_FILE not found."
  echo "Either export OP_SERVICE_ACCOUNT_TOKEN or place the SA token in $SA_TOKEN_FILE."
  exit 1
fi

# ── Env files ──────────────────────────────────────────────────────────────
if [[ ! -f .env.op ]]; then
  echo "ERROR: .env.op not found."
  echo "Copy .env.op.template to .env.op and verify vault/item IDs."
  exit 1
fi

# ── Launch via op run ──────────────────────────────────────────────────────
# op run resolves all op:// refs in .env.op and exports them as env vars.
# The inner launcher (_launch-compose.sh):
#   - "up": materializes file-backed secrets to tmpfs, then starts compose
#   - "down": stops compose, then cleans up the tmpfs files
#   - other: passes through to docker compose (token file stays if it exists)
"$OP_BIN" run --env-file=.env.op -- bash scripts/_launch-compose.sh "$@"
