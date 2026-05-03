#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# _launch-compose.sh — Inner launcher called by run-with-1password.sh via
# `op run`. At this point all op:// refs in .env.op are resolved as env vars.
#
# This script:
#   1. For "up" commands: materializes K8S_KUBELET_TOKEN to a tmpfs file,
#      then starts docker compose.
#   2. For "down" commands: stops docker compose and cleans up the tmpfs token.
#   3. For other commands: passes through to docker compose.
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

ACTION=""
for arg in "$@"; do
  case "$arg" in
    up|start) ACTION="up"; break ;;
    down|stop) ACTION="down"; break ;;
  esac
done

K8S_TOKEN_TMPDIR="${K8S_TOKEN_TMPDIR:-/dev/shm/monitoring-stack}"
K8S_TOKEN_FILE="${K8S_TOKEN_TMPDIR}/k8s-kubelet-token"

COMPOSE_ENV_FLAGS=()
if [[ -f .env.nonsecret ]]; then
  COMPOSE_ENV_FLAGS+=(--env-file .env.nonsecret)
fi

if [[ "$ACTION" == "down" ]]; then
  sudo -E docker compose "${COMPOSE_ENV_FLAGS[@]}" "$@"
  if [[ -f "$K8S_TOKEN_FILE" ]]; then
    rm -f "$K8S_TOKEN_FILE"
  fi
  rmdir "$K8S_TOKEN_TMPDIR" 2>/dev/null || true
  exit 0
fi

if [[ -n "${K8S_KUBELET_TOKEN:-}" ]]; then
  mkdir -p "$K8S_TOKEN_TMPDIR"
  chmod 700 "$K8S_TOKEN_TMPDIR"
  if [[ -d "$K8S_TOKEN_FILE" ]]; then
    rm -rf "$K8S_TOKEN_FILE"
  fi
  printf '%s' "$K8S_KUBELET_TOKEN" > "$K8S_TOKEN_FILE"
  chmod 444 "$K8S_TOKEN_FILE"
  export K8S_TOKEN_FILE
elif [[ -f "$K8S_TOKEN_FILE" ]]; then
  export K8S_TOKEN_FILE
else
  echo "WARN: K8S_KUBELET_TOKEN not set and no token file exists."
  echo "      Prometheus will skip kubelet token-backed scrapes until it is restored."
fi

sudo -E docker compose "${COMPOSE_ENV_FLAGS[@]}" "$@"
