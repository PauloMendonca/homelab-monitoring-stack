import logging
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("whatsapp-relay")


def _format_alert(alert):
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    status = alert.get("status", "unknown").upper()
    severity = labels.get("severity", "unknown")
    name = labels.get("alertname", "unnamed")
    instance = labels.get("instance", "n/a")
    summary = annotations.get("summary", "")
    description = annotations.get("description", "")
    started = alert.get("startsAt", "")
    return (
        f"[{status}] {name} ({severity})\\n"
        f"Instancia: {instance}\\n"
        f"Inicio: {started}\\n"
        f"Resumo: {summary}\\n"
        f"Detalhe: {description}"
    )


def _build_message(payload):
    source = os.getenv("ALERT_SOURCE", "monitoring")
    alerts = payload.get("alerts", [])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    chunks = [f"{source} | Alertmanager | {now}"]
    for alert in alerts[:5]:
        chunks.append(_format_alert(alert))
    if len(alerts) > 5:
        chunks.append(f"... e mais {len(alerts) - 5} alertas")
    return "\\n\\n".join(chunks)


def _send_whatsapp(message):
    base_url = os.getenv("EVOLUTION_BASE_URL", "").rstrip("/")
    instance = os.getenv("EVOLUTION_INSTANCE", "")
    api_key = os.getenv("EVOLUTION_API_KEY", "")
    number = os.getenv("WHATSAPP_TO", "")

    if not all([base_url, instance, api_key, number]):
        logger.warning("Relay not configured: missing Evolution vars")
        return False, "relay_not_configured"

    url = f"{base_url}/message/sendText/{instance}"
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "number": number,
        "text": message,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        logger.error("Evolution request failed: %s", exc)
        return False, "evolution_request_error"

    if resp.status_code >= 300:
        logger.error("Evolution returned HTTP %s: %s", resp.status_code, resp.text[:200])
        return False, f"evolution_http_{resp.status_code}"

    logger.info("WhatsApp notification sent")
    return True, "ok"


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.post("/alertmanager")
def alertmanager_webhook():
    payload = request.get_json(silent=True) or {}
    alerts = payload.get("alerts", [])
    logger.info("Received alert batch with %d alerts", len(alerts))
    message = _build_message(payload)
    ok, reason = _send_whatsapp(message)
    code = 200 if ok else 202
    return jsonify({"sent": ok, "reason": reason}), code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
