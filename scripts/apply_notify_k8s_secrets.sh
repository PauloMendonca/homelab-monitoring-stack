#!/usr/bin/env bash
set -euo pipefail

# Requires op CLI session and kubectl context pointing to MicroK8s.
# Do not commit generated values or output.

NAMESPACE="${NAMESPACE:-ai-platform}"

NOTIFY_API_KEY_REF="${NOTIFY_API_KEY_REF:-}"
EVOLUTION_API_KEY_REF="${EVOLUTION_API_KEY_REF:-}"
EVOLUTION_AUTH_KEY_REF="${EVOLUTION_AUTH_KEY_REF:-}"

if [[ -z "${NOTIFY_API_KEY_REF}" || -z "${EVOLUTION_API_KEY_REF}" || -z "${EVOLUTION_AUTH_KEY_REF}" ]]; then
  echo "Set NOTIFY_API_KEY_REF, EVOLUTION_API_KEY_REF and EVOLUTION_AUTH_KEY_REF with op://<vault_id>/<item_id>/password references." >&2
  exit 1
fi

NOTIFY_API_KEY="$(op read "${NOTIFY_API_KEY_REF}")"
EVOLUTION_API_KEY="$(op read "${EVOLUTION_API_KEY_REF}")"
EVOLUTION_AUTH_KEY="$(op read "${EVOLUTION_AUTH_KEY_REF}")"

kubectl -n "${NAMESPACE}" create secret generic notify-api-secrets \
  --from-literal=NOTIFY_API_KEY="${NOTIFY_API_KEY}" \
  --from-literal=EVOLUTION_API_KEY="${EVOLUTION_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "${NAMESPACE}" create secret generic evolution-api-secrets \
  --from-literal=AUTHENTICATION_API_KEY="${EVOLUTION_AUTH_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "notify-api-secrets and evolution-api-secrets applied in ${NAMESPACE}"
