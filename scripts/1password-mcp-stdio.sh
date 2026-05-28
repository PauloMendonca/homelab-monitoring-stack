#!/usr/bin/env bash
set -euo pipefail

HOMELAB_1PASSWORD_ROOT="${HOMELAB_1PASSWORD_ROOT:-/var/lib/homelab/1password}"
DEFAULT_TOKEN_FILE="${HOMELAB_1PASSWORD_ROOT}/op-service-account-token"
LEGACY_TOKEN_FILE="/mnt/pool_fast/db/secrets/1password-mcp/token"
TOKEN_FILE="${TOKEN_FILE:-${DEFAULT_TOKEN_FILE}}"
if [[ ! -f "$TOKEN_FILE" && -f "$LEGACY_TOKEN_FILE" ]]; then
  TOKEN_FILE="$LEGACY_TOKEN_FILE"
fi
MCP_PACKAGE="${MCP_PACKAGE:-@takescake/1password-mcp@2.4.1}"
LOG_LEVEL="${LOG_LEVEL:-info}"

if [[ ! -f "$TOKEN_FILE" ]]; then
  printf 'Token file not found: %s\n' "$TOKEN_FILE" >&2
  exit 1
fi

OP_SERVICE_ACCOUNT_TOKEN="$(tr -d '\r\n' < "$TOKEN_FILE")"

if [[ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]]; then
  printf 'Token file is empty: %s\n' "$TOKEN_FILE" >&2
  exit 1
fi

export OP_SERVICE_ACCOUNT_TOKEN

if command -v npx >/dev/null 2>&1; then
  exec npx -y "$MCP_PACKAGE" --log-level "$LOG_LEVEL"
fi

if ! command -v docker >/dev/null 2>&1; then
  printf 'Neither npx nor docker found on host.\n' >&2
  exit 1
fi

DOCKER_CMD=(docker)
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    DOCKER_CMD=(sudo -n docker)
  else
    printf 'Docker is unavailable for the current user. Add the account to the docker group or configure passwordless sudo for docker.\n' >&2
    exit 1
  fi
fi

exec "${DOCKER_CMD[@]}" run --rm -i \
  -e OP_SERVICE_ACCOUNT_TOKEN="$OP_SERVICE_ACCOUNT_TOKEN" \
  node:20-bookworm-slim \
  sh -lc "npx -y '$MCP_PACKAGE' --log-level '$LOG_LEVEL'"
