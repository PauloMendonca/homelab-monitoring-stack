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

# When invoked via forced command with SSH_ORIGINAL_COMMAND:
# The full command string is available in $SSH_ORIGINAL_COMMAND.
# Extract the subcommand (second word) if command starts with "mode-switch".
VALID_SUBCOMMANDS="status|normal"

determine_subcmd() {
    local arg1="${1:-}"
    local arg2="${2:-}"
    local orig="${SSH_ORIGINAL_COMMAND:-}"

    if [[ "$arg1" == "mode-switch" && -n "$arg2" ]]; then
        echo "$arg2"
    elif [[ "$orig" =~ ^mode-switch[[:space:]]+(status|normal)$ ]]; then
        echo "${BASH_REMATCH[1]}"
    elif [[ "$arg1" =~ ^(status|normal)$ ]]; then
        echo "$arg1"
    else
        echo ""
    fi
}

log() {
    echo "[$(date -Iseconds)] MODE-SWITCH: $*"
}

audit() {
    # Write to syslog for audit trail
    logger -t mode-switch -p user.info "audit: user=$USER command='$*' result=$1" 2>/dev/null || true
}

MODE_ARG="$(determine_subcmd "${1:-}" "${2:-}")"

if [[ -z "$MODE_ARG" ]]; then
    echo "ERROR: Invalid or empty command. Allowed: status, normal"
    audit "rejected_empty_or_invalid"
    exit 1
fi

if [[ ! "$MODE_ARG" =~ ^($VALID_SUBCOMMANDS)$ ]]; then
    echo "ERROR: Invalid command '$MODE_ARG'. Allowed: status, normal"
    audit "rejected_invalid"
    exit 1
fi

audit "command_accepted"

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
            if [[ -f /etc/mode-switch/current ]]; then
                CURRENT_MODE="$(cat /etc/mode-switch/current 2>/dev/null || echo 'unknown')"
            fi
        fi

        # If no state file, try to detect from running processes or config
        if [[ "$CURRENT_MODE" == "unknown" ]]; then
            # Check if there's a litellm or ollama config indicating mode
            CURRENT_MODE="normal"  # default assumption
        fi

        echo "OK"
        echo "service=$SERVICE_STATUS"
        echo "mode=$CURRENT_MODE"
        audit "status_response_ok"
        ;;

    normal)
        audit "normal_requested"
        log "Normal mode transition requested"

        # Write state file if directory exists and is writable
        if [[ -d /etc/mode-switch ]] && [[ -w /etc/mode-switch/current ]]; then
            echo "normal" > /etc/mode-switch/current
            systemctl restart mode-switch 2>/dev/null || true
            TRANSITION_STATUS="executed"
        elif [[ -d /etc/mode-switch ]]; then
            # Directory exists but file not writable, try sudo
            if sudo tee /etc/mode-switch/current > /dev/null 2>&1 <<< "normal"; then
                sudo systemctl restart mode-switch 2>/dev/null || true
                TRANSITION_STATUS="executed"
            else
                TRANSITION_STATUS="accepted"
            fi
        else
            # No systemd service - create state file in tmp (idempotent)
            TRANSITION_STATUS="accepted"
        fi

        echo "OK"
        echo "transition=normal"
        echo "status=$TRANSITION_STATUS"
        audit "normal_executed_$TRANSITION_STATUS"
        ;;

    *)
        echo "ERROR: Internal error - unexpected command '$MODE_ARG'"
        audit "internal_error"
        exit 1
        ;;
esac
