"""
Restricted remote executor for mode-switch operations (Phase 2).

This module provides a minimal-privilege bridge from alert-router (TrueNAS)
to the mode-switch service on MicroK8s (10.10.11.5) via SSH forced command.

Security model:
- alert-router container uses a dedicated SSH key pair (not user credentials)
- The public key is installed in ~ubuntu/.ssh/authorized_keys on 10.10.11.5
- The corresponding private key is stored as a 1Password secret
- The forced command in authorized_keys limits access to mode-switch-executor.sh
- This module only executes predetermined status/normal commands
- No shell access, no pipe, no arbitrary command execution possible

Secrets (SSH private key) are consumed via 1Password op:// references
resolved at container startup through op run --env-file.
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
    """Possible results of a mode-switch operation."""
    UNKNOWN = "unknown"
    ACTIVE = "active"
    INACTIVE = "inactive"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass
class ModeState:
    """Current state of the mode-switch system."""
    status: ModeStatus
    current_mode: str
    service_status: str
    raw_output: str


@dataclass
class ModeTransitionResult:
    """Result of a mode transition attempt."""
    success: bool
    message: str
    raw_output: str = ""


# ── Constants ────────────────────────────────────────────────────────────────

TARGET_HOST = os.getenv("MODE_SWITCH_HOST", "10.10.11.5")
SSH_USER = os.getenv("MODE_SWITCH_SSH_USER", "ubuntu")
SSH_KEY_PATH = os.getenv("MODE_SWITCH_SSH_KEY_PATH", "")  # resolved by op run
SSH_TIMEOUT = int(os.getenv("MODE_SWITCH_TIMEOUT_SECONDS", "10"))


# ── SSH key management ──────────────────────────────────────────────────────

def _get_ssh_key() -> str | None:
    """Load SSH private key from path or environment (op resolved)."""
    key_path = os.getenv("MODE_SWITCH_SSH_KEY", "")
    if key_path and Path(key_path).expanduser().exists():
        return Path(key_path).expanduser().read_text()

    # Also check direct env var content (when op resolves inline)
    key_content = os.getenv("MODE_SWITCH_SSH_KEY_CONTENT", "")
    if key_content:
        return key_content

    return None


def _write_temp_key(key_content: str) -> Path:
    """Write SSH key to a temporary file with strict permissions."""
    tmp = Path(tempfile.mktemp(suffix="_mode_switch"))
    tmp.write_text(key_content)
    tmp.chmod(0o600)
    return tmp


# ── Core executor ───────────────────────────────────────────────────────────

def _exec_remote(command: str) -> tuple[int, str, str]:
    """
    Execute a remote command via SSH with the restricted key.

    Returns: (returncode, stdout, stderr)
    """
    key_content = _get_ssh_key()
    if not key_content:
        logger.error("SSH key not available for mode-switch remote execution")
        return (1, "", "SSH key not configured")

    key_path = _write_temp_key(key_content)
    try:
        cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=5",
            "-o", "BatchMode=yes",
            "-i", str(key_path),
            f"{SSH_USER}@{TARGET_HOST}",
            command,
        ]
        logger.debug("Executing remote command via SSH: %s", command)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT,
        )
        return (result.returncode, result.stdout, result.stderr)
    finally:
        key_path.unlink(missing_ok=True)


# ── Mode status query ───────────────────────────────────────────────────────

def get_mode_status() -> ModeState:
    """
    Query the current status of the mode-switch system on 10.10.11.5.

    Returns ModeState with current mode and service status.
    """
    returncode, stdout, stderr = _exec_remote("mode-switch status")

    if returncode != 0:
        logger.warning("mode-switch status failed: %s", stderr or "non-zero exit")
        return ModeState(
            status=ModeStatus.ERROR,
            current_mode="error",
            service_status="unreachable",
            raw_output=stderr,
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
    Returns success even if already in normal mode.
    """
    returncode, stdout, stderr = _exec_remote("mode-switch normal")

    if returncode != 0:
        logger.error("mode-switch normal failed: %s", stderr or "non-zero exit")
        return ModeTransitionResult(
            success=False,
            message=f"Failed to execute normal mode transition: {stderr or 'SSH error'}",
            raw_output=stderr,
        )

    # Parse output for status
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
            message="Modo normal ativado com sucesso",
            raw_output=stdout,
        )

    return ModeTransitionResult(
        success=False,
        message=f"Modo normal rejeitado: {stdout}",
        raw_output=stdout,
    )
