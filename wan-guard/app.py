import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List

import requests
from prometheus_client import Counter, Gauge, start_http_server


logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("wan-guard")


PROM_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
POLL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
DISABLE_LOSS_PERCENT = float(os.getenv("DISABLE_LOSS_PERCENT", "20"))
DISABLE_FOR_SECONDS = int(os.getenv("DISABLE_FOR_SECONDS", "30"))
ENABLE_ZERO_LOSS_FOR_SECONDS = int(os.getenv("ENABLE_ZERO_LOSS_FOR_SECONDS", "60"))
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", "300"))
PREFERRED_WHEN_BOTH_BAD = os.getenv("PREFERRED_WHEN_BOTH_BAD", "WAN_NETMINAS_DHCP")
SECONDARY_WAN_NAME = os.getenv("SECONDARY_WAN_NAME", "WAN_VALENET_DHCP")
METRICS_PORT = int(os.getenv("METRICS_PORT", "9950"))

PF_HOST = os.getenv("PF_HOST", "10.10.10.1")
PF_USER = os.getenv("PF_USER", "paulo")
PF_SSH_KEY_PATH = os.getenv("PF_SSH_KEY_PATH", "/run/secrets/pfsense_id_rsa")
PF_KNOWN_HOSTS_PATH = os.getenv("PF_KNOWN_HOSTS_PATH", "/run/secrets/known_hosts")

WAN_INTERFACE_MAP_RAW = os.getenv(
    "WAN_INTERFACE_MAP",
    "WAN_NETMINAS_DHCP:opt1,WAN_VALENET_DHCP:wan",
).strip()


def _parse_gateway_map(raw: str) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        gateway, iface = item.split(":", 1)
        gateway = gateway.strip()
        iface = iface.strip()
        if not gateway or not iface:
            continue
        mapping[gateway] = iface
    if not mapping:
        raise RuntimeError("WAN_INTERFACE_MAP is empty or invalid")
    return mapping


actions_total = Counter(
    "wan_guard_actions_total",
    "Total gateway actions executed",
    ["gateway", "action", "result"],
)

state_gauge = Gauge(
    "wan_guard_gateway_state",
    "Gateway state (0 enabled, 1 disabled, 2 probing)",
    ["gateway"],
)

loss_gauge = Gauge(
    "wan_guard_gateway_loss_percent",
    "Last observed gateway packet loss percent",
    ["gateway"],
)

bad_streak_gauge = Gauge(
    "wan_guard_bad_streak_seconds",
    "Current high loss streak in seconds",
    ["gateway"],
)

zero_streak_gauge = Gauge(
    "wan_guard_zero_streak_seconds",
    "Current zero loss streak in seconds",
    ["gateway"],
)

last_action_ts = Gauge(
    "wan_guard_last_action_timestamp",
    "Unix timestamp of last action per gateway",
    ["gateway"],
)


@dataclass
class GatewayState:
    interface: str
    mode: str = "enabled"
    disabled_by_guard: bool = False
    bad_streak_seconds: int = 0
    zero_streak_seconds: int = 0
    last_loss: float = 0.0
    last_up: float = 1.0
    last_action_at: float = 0.0


def _ssh_base() -> List[str]:
    return [
        "ssh",
        "-i",
        PF_SSH_KEY_PATH,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        f"UserKnownHostsFile={PF_KNOWN_HOSTS_PATH}",
        "-o",
        "StrictHostKeyChecking=yes",
        f"{PF_USER}@{PF_HOST}",
    ]


def _run_ssh(remote_cmd: str, timeout: int = 20) -> str:
    cmd = _ssh_base() + [remote_cmd]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ssh command failed")
    return result.stdout


def _prom_query(query: str) -> List[dict]:
    resp = requests.get(f"{PROM_URL}/api/v1/query", params={"query": query}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"prometheus query failed: {query}")
    return data.get("data", {}).get("result", [])


def _metric_by_gateway(query: str) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for item in _prom_query(query):
        labels = item.get("metric", {})
        gateway = labels.get("gateway")
        value = float(item.get("value", [0, 0])[1])
        if not gateway:
            continue
        result[gateway] = max(value, result.get(gateway, value))
    return result


def _scrape_is_healthy() -> bool:
    result = _prom_query("max(pfsense_gateway_scrape_success)")
    if not result:
        return False
    return float(result[0].get("value", [0, 0])[1]) >= 1.0


def _interface_action(action: str, iface: str) -> None:
    if action not in {"start", "stop"}:
        raise ValueError("invalid action")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", iface):
        raise ValueError(f"invalid interface {iface}")
    out = _run_ssh(f"pfSctl -c 'interface all {action} {iface}'", timeout=30).strip()
    if out and "OK" not in out:
        logger.warning("Unexpected pfSctl output for %s %s: %s", action, iface, out)


def _can_act(gw: GatewayState, now: float) -> bool:
    return (now - gw.last_action_at) >= COOLDOWN_SECONDS


def main() -> None:
    start_http_server(METRICS_PORT)
    logger.info("wan-guard metrics on :%s", METRICS_PORT)

    gateway_ifaces = _parse_gateway_map(WAN_INTERFACE_MAP_RAW)
    logger.info("managed gateways: %s", gateway_ifaces)

    states: Dict[str, GatewayState] = {
        gateway: GatewayState(interface=iface) for gateway, iface in gateway_ifaces.items()
    }

    while True:
        loop_start = time.time()
        try:
            if not _scrape_is_healthy():
                logger.warning("Skipping cycle: pfSense scrape unhealthy")
                time.sleep(POLL_SECONDS)
                continue

            loss = _metric_by_gateway("pfsense_gateway_loss_percent")
            up = _metric_by_gateway("pfsense_gateway_up")

            for gateway, gw in states.items():
                gw_loss = loss.get(gateway)
                gw_up = up.get(gateway)

                if gw_loss is None:
                    gw.bad_streak_seconds = 0
                    gw.zero_streak_seconds = 0
                else:
                    gw.last_loss = gw_loss
                    if gw_loss >= DISABLE_LOSS_PERCENT:
                        gw.bad_streak_seconds += POLL_SECONDS
                    else:
                        gw.bad_streak_seconds = 0

                    if gw_loss == 0 and (gw_up is None or gw_up >= 1):
                        gw.zero_streak_seconds += POLL_SECONDS
                    else:
                        gw.zero_streak_seconds = 0

                if gw_up is not None:
                    gw.last_up = gw_up

                mode_value = 0 if gw.mode == "enabled" else 1 if gw.mode == "disabled" else 2
                state_gauge.labels(gateway=gateway).set(mode_value)
                loss_gauge.labels(gateway=gateway).set(gw.last_loss)
                bad_streak_gauge.labels(gateway=gateway).set(gw.bad_streak_seconds)
                zero_streak_gauge.labels(gateway=gateway).set(gw.zero_streak_seconds)
                last_action_ts.labels(gateway=gateway).set(gw.last_action_at)

            now = time.time()

            disable_candidates = [
                gateway
                for gateway, gw in states.items()
                if not gw.disabled_by_guard and gw.bad_streak_seconds >= DISABLE_FOR_SECONDS
            ]

            if PREFERRED_WHEN_BOTH_BAD in disable_candidates and SECONDARY_WAN_NAME in disable_candidates:
                disable_candidates = [PREFERRED_WHEN_BOTH_BAD]

            for gateway in sorted(disable_candidates):
                gw = states[gateway]
                enabled_count = sum(1 for s in states.values() if not s.disabled_by_guard)
                if enabled_count <= 1:
                    logger.warning("Skip disable for %s: only one WAN would remain", gateway)
                    continue
                if not _can_act(gw, now):
                    continue

                try:
                    _interface_action("stop", gw.interface)
                    gw.mode = "disabled"
                    gw.disabled_by_guard = True
                    gw.last_action_at = now
                    gw.bad_streak_seconds = 0
                    gw.zero_streak_seconds = 0
                    actions_total.labels(gateway=gateway, action="disable", result="success").inc()
                    logger.warning("Disabled gateway %s via interface %s", gateway, gw.interface)
                except Exception as exc:  # noqa: BLE001
                    actions_total.labels(gateway=gateway, action="disable", result="error").inc()
                    logger.error("Disable action failed for %s: %s", gateway, exc)

            for gateway, gw in states.items():
                if not gw.disabled_by_guard:
                    continue

                if gw.mode == "disabled" and _can_act(gw, now):
                    try:
                        _interface_action("start", gw.interface)
                        gw.mode = "probing"
                        gw.last_action_at = now
                        gw.bad_streak_seconds = 0
                        gw.zero_streak_seconds = 0
                        actions_total.labels(gateway=gateway, action="probe_start", result="success").inc()
                        logger.info("Started probe window for %s (%s)", gateway, gw.interface)
                    except Exception as exc:  # noqa: BLE001
                        actions_total.labels(gateway=gateway, action="probe_start", result="error").inc()
                        logger.error("Probe start failed for %s: %s", gateway, exc)
                    continue

                if gw.mode != "probing":
                    continue

                if gw.bad_streak_seconds >= DISABLE_FOR_SECONDS and _can_act(gw, now):
                    try:
                        _interface_action("stop", gw.interface)
                        gw.mode = "disabled"
                        gw.last_action_at = now
                        gw.bad_streak_seconds = 0
                        gw.zero_streak_seconds = 0
                        actions_total.labels(gateway=gateway, action="probe_fail_disable", result="success").inc()
                        logger.warning("Probe failed, disabled %s again", gateway)
                    except Exception as exc:  # noqa: BLE001
                        actions_total.labels(gateway=gateway, action="probe_fail_disable", result="error").inc()
                        logger.error("Probe-disable failed for %s: %s", gateway, exc)
                    continue

                if gw.zero_streak_seconds >= ENABLE_ZERO_LOSS_FOR_SECONDS:
                    gw.mode = "enabled"
                    gw.disabled_by_guard = False
                    gw.last_action_at = now
                    gw.bad_streak_seconds = 0
                    gw.zero_streak_seconds = 0
                    actions_total.labels(gateway=gateway, action="enable_confirm", result="success").inc()
                    logger.info("Gateway %s confirmed healthy and kept enabled", gateway)

        except Exception as exc:  # noqa: BLE001
            logger.exception("wan-guard loop error: %s", exc)

        elapsed = time.time() - loop_start
        sleep_for = max(1, POLL_SECONDS - int(elapsed))
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
