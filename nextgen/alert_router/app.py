import logging
import os
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, HTTPException, Request


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("alert-router")

app = FastAPI(title="alert-router", version="0.1.0")


def _fmt_alert(alert: dict) -> str:
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    return (
        f"[{alert.get('status', 'unknown').upper()}] {labels.get('alertname', 'unnamed')} ({labels.get('severity', 'unknown')})\n"
        f"Instancia: {labels.get('instance', 'n/a')}\n"
        f"Inicio: {alert.get('startsAt', '')}\n"
        f"Resumo: {annotations.get('summary', '')}\n"
        f"Detalhe: {annotations.get('description', '')}"
    )


def _group_by_severity(alerts: list[dict]) -> dict[str, list[dict]]:
    grouped = {"critical": [], "general": []}
    for alert in alerts:
        severity = str((alert.get("labels", {}) or {}).get("severity", "")).lower()
        if severity == "critical":
            grouped["critical"].append(alert)
        else:
            grouped["general"].append(alert)
    return grouped


def _destination_numbers(kind: str) -> list[str]:
    general = os.getenv("WHATSAPP_TO_GENERAL", "")
    critical = os.getenv("WHATSAPP_TO_CRITICAL", "")
    if kind == "critical":
        out = [value for value in [general, critical] if value]
        return list(dict.fromkeys(out))
    return [general] if general else []


def _build_message(alerts: list[dict]) -> str:
    source = os.getenv("ALERT_SOURCE", "homelab-monitoring")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    parts = [f"{source} | Alertmanager | {now}"]
    for alert in alerts[:5]:
        parts.append(_fmt_alert(alert))
    if len(alerts) > 5:
        parts.append(f"... e mais {len(alerts) - 5} alertas")
    return "\n\n".join(parts)


def _publish_message(policy: str, text: str, recipients: list[str]) -> None:
    notify_url = os.getenv("NOTIFY_API_URL", "").rstrip("/")
    api_key = os.getenv("NOTIFY_API_KEY", "")
    if not notify_url or not api_key:
        raise HTTPException(status_code=500, detail="notify_api_not_configured")

    response = requests.post(
        f"{notify_url}/v1/messages/policy",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "source": "alertmanager",
            "priority": policy,
            "policy": policy,
            "recipients": recipients,
        },
        timeout=15,
    )
    if response.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"notify_api_http_{response.status_code}")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/alertmanager")
async def alertmanager_webhook(request: Request) -> dict[str, int]:
    payload = await request.json()
    alerts = payload.get("alerts", [])
    grouped = _group_by_severity(alerts)

    total_published = 0
    for policy in ("general", "critical"):
        policy_alerts = grouped[policy]
        if not policy_alerts:
            continue
        recipients = _destination_numbers(policy)
        if not recipients:
            logger.warning("Skipping %s alerts, no recipients configured", policy)
            continue

        message = _build_message(policy_alerts)
        _publish_message(policy, message, recipients)
        total_published += 1

    logger.info("Processed %d alerts in %d publications", len(alerts), total_published)
    return {"alerts": len(alerts), "publications": total_published}
