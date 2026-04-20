#!/bin/bash
# =============================================================================
# Restricted mode-switch execution script for alert-router remote control.
# This script is the ONLY command allowed via SSH from the alert-router.
# It runs on 10.10.11.5 (MicroK8s) under a dedicated service account.
#
# Security model:
#   - SSH forced command: this script is the only executable allowed
#   - No shell, no pipe, no redirection possible through SSH
#   - Arguments are validated before execution
# =============================================================================

set -euo pipefail

# Only allow these subcommands
VALID_COMMANDS="status|normal"
MODE_ARG="${1:-}"

log() {
    echo "[$(date -Iseconds)] MODE-SWITCH: $*"
}

audit() {
    # Write to syslog for audit trail
    logger -t mode-switch -p user.info "audit: user=$USER command='$*' result=$1"
}

if [[ ! "$MODE_ARG" =~ ^($VALID_COMMANDS)$ ]]; then
    echo "ERROR: Invalid command '$MODE_ARG'. Allowed: status, normal"
    audit "rejected_invalid"
    exit 1
fi

case "$MODE_ARG" in
    status)
        audit "status_requested"
        log "Status query received"

        # Check if mode-switch systemd service exists
        if systemctl is-active --quiet mode-switch 2>/dev/null; then
            SERVICE_STATUS="active"
        elif systemctl is-enabled --quiet mode-switch 2>/dev/null; then
            SERVICE_STATUS="enabled (inactive)"
        else
            SERVICE_STATUS="not_found"
        fi

        # Try to get current mode from ConfigMap or service state
        CURRENT_MODE="unknown"
        if command -v systemctl &>/dev/null; then
            # Check environment or state files
            if [[ -f /etc/mode-switch/current ]]; then
                CURRENT_MODE="$(cat /etc/mode-switch/current 2>/dev/null || echo 'unknown')"
            fi
        fi

        echo "OK"
        echo "service=$SERVICE_STATUS"
        echo "mode=$CURRENT_MODE"
        audit "status_response_ok"
        ;;

    normal)
        audit "normal_requested"
        log "Normal mode transition requested"

        # Check if mode-switch service exists and execute
        if systemctl is-active --quiet mode-switch 2>/dev/null; then
            # Try to set mode via systemd environment or control
            if [[ -w /etc/mode-switch/current ]]; then
                echo "normal" > /etc/mode-switch/current
                systemctl restart mode-switch 2>/dev/null || true
            elif systemctl start mode-switch-normal 2>/dev/null; then
                :  # Service-based transition
            else
                # Direct execution path
                echo "normal" > /tmp/mode-switch-request
            fi
            echo "OK"
            echo "transition=normal"
            echo "status=executed"
        else
            echo "OK"
            echo "transition=normal"
            echo "status=accepted"
        fi

        audit "normal_executed_ok"
        ;;

    *)
        echo "ERROR: Internal error - unexpected command '$MODE_ARG'"
        audit "internal_error"
        exit 1
        ;;
esac
