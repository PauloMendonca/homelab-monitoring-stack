import os
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Dict, List

from flask import Flask, Response
from prometheus_client import CollectorRegistry, Gauge, generate_latest

app = Flask(__name__)


LEASE_BLOCK_RE = re.compile(r"lease\s+([0-9.]+)\s*\{(.*?)\}", re.DOTALL)
STATE_RE = re.compile(r"binding state\s+(\w+);")
MAC_RE = re.compile(r"hardware ethernet\s+([0-9a-f:]{17});", re.IGNORECASE)
HOSTNAME_RE = re.compile(r"client-hostname\s+\"([^\"]+)\";")


def _run_remote(cmd: str) -> str:
    host = os.getenv("PF_HOST", "10.10.10.1")
    user = os.getenv("PF_USER", "paulo")
    key = os.getenv("PF_SSH_KEY_PATH", "/run/secrets/pfsense_id_rsa")
    known_hosts = os.getenv("PF_KNOWN_HOSTS_PATH", "/run/secrets/known_hosts")

    ssh_cmd = [
        "ssh",
        "-i",
        key,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "StrictHostKeyChecking=yes",
        f"{user}@{host}",
        cmd,
    ]

    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=20, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ssh command failed")
    return result.stdout


def _to_ms(value: str) -> float:
    if value.endswith("ms"):
        return float(value[:-2])
    return float(value)


def _to_percent(value: str) -> float:
    if value.endswith("%"):
        return float(value[:-1])
    return float(value)


def _safe_label(value: str) -> str:
    v = (value or "").strip()
    return v if v else "unknown"


def _ip_sort_key(ip: str) -> str:
    parts = ip.split(".")
    if len(parts) != 4:
        return "999.999.999.999"
    try:
        return ".".join(f"{int(part):03d}" for part in parts)
    except ValueError:
        return "999.999.999.999"


def _parse_gateway_status(output: str) -> List[Dict[str, str]]:
    gateways: List[Dict[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Name") or line.startswith("----"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 8:
            continue
        gateways.append(
            {
                "name": parts[0],
                "monitor": parts[1],
                "source": parts[2],
                "delay_ms": str(_to_ms(parts[3])),
                "stddev_ms": str(_to_ms(parts[4])),
                "loss_percent": str(_to_percent(parts[5])),
                "status": parts[6],
                "substatus": parts[7],
            }
        )
    return gateways


def _parse_dhcp_dynamic_leases(leases_text: str) -> Dict[str, Dict[str, str]]:
    current_by_ip: Dict[str, Dict[str, str]] = {}

    for ip, body in LEASE_BLOCK_RE.findall(leases_text):
        state_match = STATE_RE.search(body)
        mac_match = MAC_RE.search(body)
        host_match = HOSTNAME_RE.search(body)

        current_by_ip[ip] = {
            "ip": ip,
            "state": state_match.group(1).lower() if state_match else "unknown",
            "mac": mac_match.group(1).lower() if mac_match else "unknown",
            "hostname": host_match.group(1) if host_match else "unknown",
        }

    return current_by_ip


def _parse_dhcp_static_leases(config_xml: str) -> Dict[str, Dict[str, str]]:
    static_by_ip: Dict[str, Dict[str, str]] = {}

    root = ET.fromstring(config_xml)
    dhcpd = root.find("dhcpd")
    if dhcpd is None:
        return static_by_ip

    for iface in list(dhcpd):
        for staticmap in iface.findall("staticmap"):
            ip = (staticmap.findtext("ipaddr") or "").strip()
            mac = (staticmap.findtext("mac") or "").strip().lower()
            hostname = (staticmap.findtext("hostname") or staticmap.findtext("descr") or "unknown").strip()
            if not ip or not mac:
                continue
            static_by_ip[ip] = {
                "ip": ip,
                "mac": mac,
                "hostname": hostname if hostname else "unknown",
            }

    return static_by_ip


@app.get("/metrics")
def metrics() -> Response:
    registry = CollectorRegistry()

    gateway_scrape_success = Gauge("pfsense_gateway_scrape_success", "1 when gateway scrape is successful", registry=registry)
    gateway_scrape_error = Gauge("pfsense_gateway_scrape_error", "1 when gateway scrape has errors", ["reason"], registry=registry)

    gw_up = Gauge(
        "pfsense_gateway_up",
        "Gateway online status from pfSense",
        ["gateway", "monitor_ip", "source_ip"],
        registry=registry,
    )
    gw_delay = Gauge("pfsense_gateway_delay_ms", "Gateway delay in milliseconds", ["gateway"], registry=registry)
    gw_stddev = Gauge("pfsense_gateway_stddev_ms", "Gateway jitter/stddev in milliseconds", ["gateway"], registry=registry)
    gw_loss = Gauge("pfsense_gateway_loss_percent", "Gateway packet loss in percent", ["gateway"], registry=registry)
    gw_in_routing = Gauge("pfsense_gateway_in_routing", "Gateway considered in routing/load balancing", ["gateway"], registry=registry)

    lb_total = Gauge("pfsense_loadbalancer_wans_total", "Total WAN gateways considered in load balancing", registry=registry)
    lb_routing = Gauge("pfsense_loadbalancer_wans_in_routing", "WAN gateways currently in routing", registry=registry)
    lb_all = Gauge("pfsense_loadbalancer_all_wans_in_routing", "1 when all WAN gateways are in routing", registry=registry)

    dhcp_scrape_success = Gauge("pfsense_dhcp_scrape_success", "1 when DHCP lease scrape is successful", registry=registry)
    dhcp_scrape_error = Gauge("pfsense_dhcp_scrape_error", "1 when DHCP lease scrape has errors", ["reason"], registry=registry)
    dhcp_lease_info = Gauge(
        "pfsense_dhcp_lease_info",
        "DHCP lease info for active dynamic leases and all static leases",
        ["ip", "ip_sort", "mac", "hostname", "lease_type", "lease_order", "active"],
        registry=registry,
    )
    dhcp_lease_dashboard_info = Gauge(
        "pfsense_dhcp_lease_dashboard_info",
        "DHCP lease rows for dashboard (active dynamic + all static)",
        ["ip", "ip_sort", "mac", "hostname", "lease_type", "lease_order", "active"],
        registry=registry,
    )

    try:
        gateway_output = _run_remote("pfSsh.php playback gatewaystatus")
        gateways = _parse_gateway_status(gateway_output)
        if not gateways:
            raise RuntimeError("no gateways parsed")

        routing_count = 0
        for gw in gateways:
            status = gw["status"].lower()
            is_up = 1.0 if status == "online" else 0.0
            if is_up == 1.0:
                routing_count += 1

            gw_name = _safe_label(gw["name"])
            gw_up.labels(
                gateway=gw_name,
                monitor_ip=_safe_label(gw["monitor"]),
                source_ip=_safe_label(gw["source"]),
            ).set(is_up)
            gw_delay.labels(gateway=gw_name).set(float(gw["delay_ms"]))
            gw_stddev.labels(gateway=gw_name).set(float(gw["stddev_ms"]))
            gw_loss.labels(gateway=gw_name).set(float(gw["loss_percent"]))
            gw_in_routing.labels(gateway=gw_name).set(is_up)

        total = len(gateways)
        lb_total.set(total)
        lb_routing.set(routing_count)
        lb_all.set(1 if total > 0 and routing_count == total else 0)

        gateway_scrape_success.set(1)
    except Exception as exc:  # noqa: BLE001
        gateway_scrape_success.set(0)
        gateway_scrape_error.labels(reason=str(exc)[:120]).set(1)

    try:
        leases_text = _run_remote("cat /var/dhcpd/var/db/dhcpd.leases")
        config_text = _run_remote("cat /conf/config.xml")

        dynamic_by_ip = _parse_dhcp_dynamic_leases(leases_text)
        static_by_ip = _parse_dhcp_static_leases(config_text)
        static_ips = set(static_by_ip.keys())

        for ip, dyn in dynamic_by_ip.items():
            is_active = dyn.get("state") == "active"
            if not is_active:
                continue
            if ip in static_ips:
                continue
            dhcp_lease_info.labels(
                ip=_safe_label(ip),
                ip_sort=_ip_sort_key(ip),
                mac=_safe_label(dyn.get("mac", "unknown")).lower(),
                hostname=_safe_label(dyn.get("hostname", "unknown")),
                lease_type="dynamic",
                lease_order="1",
                active="1",
            ).set(1)
            dhcp_lease_dashboard_info.labels(
                ip=_safe_label(ip),
                ip_sort=_ip_sort_key(ip),
                mac=_safe_label(dyn.get("mac", "unknown")).lower(),
                hostname=_safe_label(dyn.get("hostname", "unknown")),
                lease_type="dynamic",
                lease_order="1",
                active="1",
            ).set(1)

        for ip, st in static_by_ip.items():
            dyn = dynamic_by_ip.get(ip, {})
            is_active = 1 if dyn.get("state") == "active" else 0
            hostname = st.get("hostname", "unknown")
            if hostname == "unknown" and dyn.get("hostname"):
                hostname = dyn.get("hostname", "unknown")
            dhcp_lease_info.labels(
                ip=_safe_label(ip),
                ip_sort=_ip_sort_key(ip),
                mac=_safe_label(st.get("mac", "unknown")).lower(),
                hostname=_safe_label(hostname),
                lease_type="static",
                lease_order="0",
                active=str(is_active),
            ).set(1)
            dhcp_lease_dashboard_info.labels(
                ip=_safe_label(ip),
                ip_sort=_ip_sort_key(ip),
                mac=_safe_label(st.get("mac", "unknown")).lower(),
                hostname=_safe_label(hostname),
                lease_type="static",
                lease_order="0",
                active=str(is_active),
            ).set(1)

        dhcp_scrape_success.set(1)
    except Exception as exc:  # noqa: BLE001
        dhcp_scrape_success.set(0)
        dhcp_scrape_error.labels(reason=str(exc)[:120]).set(1)

    return Response(generate_latest(registry), mimetype="text/plain; version=0.0.4")


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9940)
