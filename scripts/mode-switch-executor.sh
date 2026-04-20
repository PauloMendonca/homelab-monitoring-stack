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
# Extract the subcommand (second word) from command string.
VALID_SUBCOMMANDS="status|normal"

determine_subcmd() {
    local orig="${SSH_ORIGINAL_COMMAND:-}"
    if [[ "$orig" =~ ^mode-switch[[:space:]]+(status|normal)$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo ""
    fi
}

log() {
    echo "[$(date -Iseconds)] MODE-SWITCH: $*"
}

audit() {
    logger -t mode-switch -p user.info "audit: user=$USER command='$*' result=$1" 2>/dev/null || true
}

MODE_ARG="$(determine_subcmd)"

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
        log "Status query received - delegating to real mode-switch"

        # Call the real mode-switch command and capture output
        REAL_OUTPUT=$(/usr/local/bin/mode-switch status 2>&1)
        REAL_EXIT=$?

        if [[ $REAL_EXIT -ne 0 ]]; then
            echo "ERROR: mode-switch status failed with exit code $REAL_EXIT"
            audit "status_failed"
            exit 1
        fi

        # Parse real output for structured fields (strip ANSI color codes)
        # Real output format:
        #   Current desired mode: [0;32mnormal[0m
        #   Actual system state: [1;33mnormal (MicroK8s running) [node Ready][0m
        #   Alignment: [0;32maligned[0m

        strip_ansi() { sed 's/\x1b\[[0-9;]*m//g'; }

        DESIRED_MODE=$(echo "$REAL_OUTPUT" | grep "Current desired mode:" | sed 's/.*: *//' | strip_ansi)
        SYSTEM_STATE=$(echo "$REAL_OUTPUT" | grep "Actual system state:" | sed 's/  Actual system state: *//' | strip_ansi)
        ALIGNMENT=$(echo "$REAL_OUTPUT" | grep "Alignment:" | sed 's/  Alignment: *//' | strip_ansi)

        # Map alignment to service status
        case "$ALIGNMENT" in
            aligned) SERVICE_STATUS="active" ;;
            misaligned) SERVICE_STATUS="misaligned" ;;
            *) SERVICE_STATUS="unknown" ;;
        esac

        # Output structured format for executor.py to parse
        echo "OK"
        echo "service=$SERVICE_STATUS"
        echo "mode=$DESIRED_MODE"
        echo "alignment=$ALIGNMENT"
        echo "raw_output_lines=3"
        audit "status_response_ok"
        ;;

    normal)
        audit "normal_requested"
        log "Normal mode transition requested"

        # Call the real mode-switch command
        REAL_OUTPUT=$(/usr/local/bin/mode-switch switch normal 2>&1)
        REAL_EXIT=$?

        if [[ $REAL_EXIT -ne 0 ]]; then
            echo "ERROR: mode-switch switch normal failed with exit code $REAL_EXIT"
            echo "Output: $REAL_OUTPUT"
            audit "normal_failed"
            exit 1
        fi

        # Check if actually switched or already in normal
        if echo "$REAL_OUTPUT" | grep -qi "already"; then
            echo "OK"
            echo "transition=normal"
            echo "status=already_set"
            audit "normal_already_set"
        else
            echo "OK"
            echo "transition=normal"
            echo "status=switched"
            audit "normal_switched"
        fi
        ;;

    *)
        echo "ERROR: Internal error - unexpected command '$MODE_ARG'"
        audit "internal_error"
        exit 1
        ;;
esac
