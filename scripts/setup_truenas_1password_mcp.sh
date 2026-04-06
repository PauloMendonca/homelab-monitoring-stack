#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-svc_1password_mcp}"
SERVICE_GROUP="${SERVICE_GROUP:-}"
BIN_DIR="${BIN_DIR:-/opt/homelab/bin}"
SECRETS_DIR="${SECRETS_DIR:-/mnt/pool_fast/db/secrets/1password-mcp}"
TOKEN_FILE="${TOKEN_FILE:-$SECRETS_DIR/token}"
SOURCE_WRAPPER="${SOURCE_WRAPPER:-$(pwd)/scripts/1password-mcp-stdio.sh}"
TARGET_WRAPPER="${TARGET_WRAPPER:-$BIN_DIR/1password-mcp-stdio.sh}"

if [[ "$EUID" -ne 0 ]]; then
  printf 'Run as root (sudo).\n' >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  if command -v useradd >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER" || true
  fi
fi

if [[ -z "$SERVICE_GROUP" ]]; then
  SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  printf 'Service user not available; set SERVICE_USER to an existing account.\n' >&2
  exit 1
fi

install -d -m 0755 "$BIN_DIR"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$SECRETS_DIR"

if [[ ! -f "$SOURCE_WRAPPER" ]]; then
  printf 'Wrapper source not found: %s\n' "$SOURCE_WRAPPER" >&2
  exit 1
fi

install -m 0750 -o root -g "$SERVICE_GROUP" "$SOURCE_WRAPPER" "$TARGET_WRAPPER"

if [[ ! -f "$TOKEN_FILE" ]]; then
  printf 'Create token file and set permissions:\n' >&2
  printf '  sudo install -m 0600 -o %s -g %s /dev/null %s\n' "$SERVICE_USER" "$SERVICE_GROUP" "$TOKEN_FILE" >&2
  printf '  sudo sh -c '\''cat > %s'\''   # paste OP_SERVICE_ACCOUNT_TOKEN\n' "$TOKEN_FILE" >&2
  exit 2
fi

chown "$SERVICE_USER:$SERVICE_GROUP" "$TOKEN_FILE"
chmod 0600 "$TOKEN_FILE"

printf 'Setup complete.\n'
printf 'Validate with:\n'
printf '  sudo -u %s TOKEN_FILE=%s %s --help\n' "$SERVICE_USER" "$TOKEN_FILE" "$TARGET_WRAPPER"
