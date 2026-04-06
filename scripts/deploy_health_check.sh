#!/bin/sh
# deploy_health_check.sh — prepara e instala o health check no pfSense
# Uso: ./deploy_health_check.sh
#
# Requer: SSH access to pfSense as paulo, secrets set below or via env vars.
# Secrets can be sourced from 1Password before running this script.

set -eu

PFSENSE_HOST="10.10.10.1"
PFSENSE_USER="paulo"
SSH_KEY="${HOME}/.ssh/id_rsa"
SCRIPT_SRC="$(dirname "$0")/pfsense-server-health-check.sh"
REMOTE_DIR="/home/paulo/bin"
REMOTE_SCRIPT="${REMOTE_DIR}/server_health_check.sh"

# ── Secrets (set via environment or edit here for one-time deploy) ──
NOTIFY_API_KEY="${NOTIFY_API_KEY:-}"
SMTP_TO="${SMTP_TO:-}"
SMTP_FROM="${SMTP_FROM:-}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASS="${SMTP_PASS:-}"

if [ -z "${NOTIFY_API_KEY}" ]; then
    echo "ERROR: NOTIFY_API_KEY not set. Export it or set inline." >&2
    exit 1
fi

echo "==> Preparing script with injected secrets..."
tmp=$(mktemp)
sed \
    -e "s|__NOTIFY_API_KEY__|${NOTIFY_API_KEY}|g" \
    -e "s|__SMTP_TO__|${SMTP_TO}|g" \
    -e "s|__SMTP_FROM__|${SMTP_FROM}|g" \
    -e "s|__SMTP_USER__|${SMTP_USER}|g" \
    -e "s|__SMTP_PASS__|${SMTP_PASS}|g" \
    "${SCRIPT_SRC}" > "${tmp}"

echo "==> Creating remote directory ${REMOTE_DIR}..."
ssh -o BatchMode=yes -i "${SSH_KEY}" "${PFSENSE_USER}@${PFSENSE_HOST}" \
    "mkdir -p ${REMOTE_DIR}"

echo "==> Copying script to pfSense..."
scp -o BatchMode=yes -i "${SSH_KEY}" "${tmp}" \
    "${PFSENSE_USER}@${PFSENSE_HOST}:${REMOTE_SCRIPT}"

echo "==> Setting permissions..."
ssh -o BatchMode=yes -i "${SSH_KEY}" "${PFSENSE_USER}@${PFSENSE_HOST}" \
    "chmod 700 ${REMOTE_SCRIPT}"

rm -f "${tmp}"

echo "==> Verifying installation..."
ssh -o BatchMode=yes -i "${SSH_KEY}" "${PFSENSE_USER}@${PFSENSE_HOST}" \
    "ls -la ${REMOTE_SCRIPT}; head -3 ${REMOTE_SCRIPT}"

echo ""
echo "==> Script installed at ${PFSENSE_HOST}:${REMOTE_SCRIPT}"
echo ""
echo "Next steps:"
echo "  1) Test manually:  ssh ${PFSENSE_USER}@${PFSENSE_HOST} '/bin/sh ${REMOTE_SCRIPT}'"
echo "  2) Add cron entry on pfSense (needs root/admin access via Web UI):"
echo "     System > Cron (package) or /etc/cron.d/:"
echo "     */1 * * * * paulo /bin/sh ${REMOTE_SCRIPT}"
echo ""
echo "Done."
