"""
Restricted remote executor for mode-switch operations (Phase 2).

Security model:
- Dedicated SSH key pair (mode_switch_id_ed25519) stored in secrets/
- Host key pinning via known_hosts file in secrets/
- Key consumed via volume mount (not inline content)
- Only 'status' and 'normal' commands accepted via forced command on target
- No StrictHostKeyChecking=no — uses proper known_hosts
"""

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger("alert-router.mode-executor")


class ModeStatus(Enum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    INACTIVE = "inactive"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass
class ModeState:
    status: ModeStatus
    current_mode: str
    service_status: str
    raw_output: str


@dataclass
class ModeTransitionResult:
    success: bool
    message: str
    raw_output: str = ""


# ── Configuration from environment ─────────────────────────────────────────

TARGET_HOST = os.getenv("MODE_SWITCH_HOST", "10.10.11.5")
SSH_USER = os.getenv("MODE_SWITCH_SSH_USER", "paulo")
SSH_KEY_PATH = os.getenv("MODE_SWITCH_SSH_KEY_PATH", "/run/secrets/mode_switch_id_ed25519")
SSH_KNOWN_HOSTS_PATH = os.getenv("MODE_SWITCH_SSH_KNOWN_HOSTS_PATH", "/run/secrets/known_hosts")
SSH_TIMEOUT = int(os.getenv("MODE_SWITCH_TIMEOUT_SECONDS", "10"))


# ── Core SSH executor ────────────────────────────────────────────────────────

def _exec_remote(command: str) -> tuple[int, str, str]:
    """
    Execute a remote command via SSH with host key pinning.

    Security features:
    - Uses known_hosts file (not /dev/null) for host key verification
    - StrictHostKeyChecking=yes (never bypassed)
    - BatchMode=yes (no password prompts)
    - Runs only the predetermined command via forced command on target
    """
    key_path = Path(SSH_KEY_PATH)
    known_hosts_path = Path(SSH_KNOWN_HOSTS_PATH)

    if not key_path.exists():
        logger.error("SSH key not found at %s", key_path)
        return (1, "", f"SSH key not found: {key_path}")

    if not known_hosts_path.exists():
        logger.error("known_hosts not found at %s", known_hosts_path)
        return (1, "", f"known_hosts not found: {known_hosts_path}")

    cmd = [
        "ssh",
        "-i", str(key_path),
        "-o", "BatchMode=yes",
        "-o", f"UserKnownHostsFile={known_hosts_path}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"ConnectTimeout={SSH_TIMEOUT}",
        f"{SSH_USER}@{TARGET_HOST}",
        command,
    ]

    logger.debug("SSH executing: %s %s@%s %s", cmd[0], SSH_USER, TARGET_HOST, command)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=SSH_TIMEOUT + 5,
    )

    return (result.returncode, result.stdout, result.stderr)


# ── Mode status query ───────────────────────────────────────────────────────

def get_mode_status() -> ModeState:
    """
    Query the current status of the mode-switch system on 10.10.11.5.

    The forced command on the target accepts only 'mode-switch status' or
    'mode-switch normal', preventing any arbitrary command execution.
    """
    returncode, stdout, stderr = _exec_remote("mode-switch status")

    if returncode != 0:
        logger.warning("mode-switch status failed (code=%d): %s", returncode, stderr or "non-zero exit")
        return ModeState(
            status=ModeStatus.ERROR,
            current_mode="error",
            service_status="unreachable",
            raw_output=stderr or f"exit code {returncode}",
        )

    lines = stdout.strip().splitlines()
    mode = "unknown"
    service = "unknown"

    for line in lines:
        if line.startswith("mode="):
            mode = line.split("=", 1)[1].strip()
        elif line.startswith("service="):
            service = line.split("=", 1)[1].strip()

    status: ModeStatus
    if service == "active":
        status = ModeStatus.ACTIVE
    elif service == "not_found":
        status = ModeStatus.NOT_FOUND
    elif service == "unreachable":
        status = ModeStatus.ERROR
    else:
        status = ModeStatus.UNKNOWN

    return ModeState(
        status=status,
        current_mode=mode,
        service_status=service,
        raw_output=stdout,
    )


# ── Mode transition ─────────────────────────────────────────────────────────

def set_mode_normal() -> ModeTransitionResult:
    """
    Request transition to normal mode on 10.10.11.5.

    Idempotent: repeating this command is safe and will not break state.
    The forced command only accepts 'mode-switch normal'.
    """
    returncode, stdout, stderr = _exec_remote("mode-switch normal")

    if returncode != 0:
        logger.error("mode-switch normal failed (code=%d): %s", returncode, stderr or "non-zero exit")
        return ModeTransitionResult(
            success=False,
            message=f"Transicao recusada: {stderr or f'exit code {returncode}'}",
            raw_output=stderr,
        )

    lines = stdout.strip().splitlines()
    transition = None
    status = None
    for line in lines:
        if line.startswith("transition="):
            transition = line.split("=", 1)[1].strip()
        elif line.startswith("status="):
            status = line.split("=", 1)[1].strip()

    if transition == "normal" and status in ("executed", "accepted"):
        return ModeTransitionResult(
            success=True,
            message="Transicao para modo normal aplicada",
            raw_output=stdout,
        )

    return ModeTransitionResult(
        success=False,
        message=f"Resposta inesperada do target: {stdout}",
        raw_output=stdout,
    )
