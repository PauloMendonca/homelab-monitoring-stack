#!/usr/bin/env bash
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-svc_1password_mcp}"
SERVICE_GROUP="${SERVICE_GROUP:-}"
SERVICE_SHELL="${SERVICE_SHELL:-/usr/bin/bash}"
HOMELAB_1PASSWORD_ROOT="${HOMELAB_1PASSWORD_ROOT:-/var/lib/homelab/1password-mcp}"
SECRETS_DIR="${SECRETS_DIR:-${HOMELAB_1PASSWORD_ROOT}}"
BIN_DIR="${BIN_DIR:-/opt/homelab/bin}"
TOKEN_FILE="${TOKEN_FILE:-${SECRETS_DIR}/op-service-account-token}"
SOURCE_WRAPPER="${SOURCE_WRAPPER:-$(pwd)/scripts/1password-mcp-stdio.sh}"
TARGET_WRAPPER="${TARGET_WRAPPER:-$BIN_DIR/1password-mcp-stdio.sh}"
AUTHORIZED_KEY_FILE="${AUTHORIZED_KEY_FILE:-}"

if [[ "$EUID" -ne 0 ]]; then
  printf 'Run as root (sudo).\n' >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  if command -v useradd >/dev/null 2>&1; then
    useradd --system --create-home --shell "$SERVICE_SHELL" "$SERVICE_USER" || true
  fi
fi

if [[ -z "$SERVICE_GROUP" ]]; then
  SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  printf 'Service user not available; set SERVICE_USER to an existing account.\n' >&2
  exit 1
fi

usermod --shell "$SERVICE_SHELL" "$SERVICE_USER"

install -d -m 0755 "$BIN_DIR"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$SECRETS_DIR"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "/home/$SERVICE_USER/.ssh"

if [[ ! -f "$SOURCE_WRAPPER" ]]; then
  printf 'Wrapper source not found: %s\n' "$SOURCE_WRAPPER" >&2
  exit 1
fi

install -m 0750 -o root -g "$SERVICE_GROUP" "$SOURCE_WRAPPER" "$TARGET_WRAPPER"

if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "$SERVICE_USER"
fi

if [[ -n "$AUTHORIZED_KEY_FILE" ]]; then
  if [[ ! -f "$AUTHORIZED_KEY_FILE" ]]; then
    printf 'Authorized key file not found: %s\n' "$AUTHORIZED_KEY_FILE" >&2
    exit 1
  fi

  AUTHORIZED_KEY_CONTENT="$(tr -d '\r' < "$AUTHORIZED_KEY_FILE")"
  AUTHORIZED_KEYS_PATH="/home/$SERVICE_USER/.ssh/authorized_keys"
  install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /dev/null "$AUTHORIZED_KEYS_PATH"
  printf 'command="%s",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,restrict %s\n' "$TARGET_WRAPPER" "$AUTHORIZED_KEY_CONTENT" > "$AUTHORIZED_KEYS_PATH"
  chown "$SERVICE_USER:$SERVICE_GROUP" "$AUTHORIZED_KEYS_PATH"
  chmod 0600 "$AUTHORIZED_KEYS_PATH"
fi

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
