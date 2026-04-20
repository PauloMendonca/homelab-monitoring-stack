import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("alert-router")

app = FastAPI(title="alert-router", version="0.2.0")

# ── In-memory dedup cache (message_id -> True), wiped on restart ─────────────
_processed_ids: dict[str, bool] = {}
_DEDUP_MAX = 10_000


def _mask_number(number: str) -> str:
    """Mask all but last 4 digits of a phone number for safe logging."""
    if not number or len(number) < 4:
        return "****"
    return f"***{number[-4:]}"


# ── Existing Alertmanager helpers ───────────────────────────────────────────

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


# ── Evolution API outbound (WhatsApp reply) ─────────────────────────────────

def _send_whatsapp(text: str, recipient: str) -> tuple[bool, str]:
    """Send a single WhatsApp text message via Evolution API."""
    base_url = os.getenv("EVOLUTION_BASE_URL", "").rstrip("/")
    instance = os.getenv("EVOLUTION_INSTANCE", "")
    api_key = os.getenv("EVOLUTION_API_KEY", "")

    if not all([base_url, instance, api_key]):
        return False, "evolution_not_configured"

    url = f"{base_url}/message/sendText/{instance}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    payload = {"number": recipient, "text": text}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
    except requests.RequestException as exc:
        return False, f"request_error:{exc}"

    if response.status_code >= 300:
        return False, f"http_{response.status_code}"

    return True, "ok"


# ── WhatsApp command parser (Phase 1 — no execution) ─────────────────────────

def _cmd_help() -> str:
    return (
        "🤖 *Modo — Comando WhatsApp*\n\n"
        "Comandos disponiveis:\n"
        "  /modo ajuda     — Esta lista\n"
        "  /modo status    — Estado atual do sistema de modos\n"
        "  /modo normal    — Ativar modo normal *(em breve)*\n"
        "  /modo gaming    — Ativar modo gaming *(em breve)*\n\n"
        "⚠️ Fase 1: canal ativo, execucao das trocas prevista para Fase 2."
    )


def _cmd_status() -> str:
    return (
        "📊 *Modo — Status*\n\n"
        "🟢 Canal WhatsApp: ATIVO\n"
        "🟡 Execucao de modos: AGUARDANDO FASE 2\n\n"
        "Estado atual: *nao implementado ainda*\n"
        "Cenario ativo: *nao implementado ainda*\n\n"
        "Para ajuda: /modo ajuda"
    )


def _cmd_not_ready(subcmd: str) -> str:
    return (
        f"⚠️ Comando `/modo {subcmd}` ainda não está ativo.\n"
        "A execução de troca de modo será habilitada na *Fase 2*.\n\n"
        "Por enquanto, o canal está funcionando para consulta.\n"
        "Para status: /modo status\n"
        "Para ajuda: /modo ajuda"
    )


# _COMMANDS must be defined AFTER all handler functions so Python can resolve them
_COMMANDS = {
    "modo": {
        "help":    ("/modo ajuda",    _cmd_help),
        "status":  ("/modo status",   _cmd_status),
        "normal":  ("/modo normal",   _cmd_not_ready),
        "gaming":  ("/modo gaming",   _cmd_not_ready),
    },
}


def _parse_command(text: str) -> tuple[str | None, str | None]:
    """Parse a WhatsApp command text. Returns (subcmd, action) or (None, None)."""
    text = text.strip()
    m = re.match(r"^/modo\s+(\w+)", text)
    if not m:
        return None, None
    return m.group(1), "execute"


def _normalize_br_mobile_number(number: str) -> str:
    """
    Normalize Brazilian mobile numbers that may be missing the 9th digit.

    WhatsApp sometimes sends numbers from the same device in two forms:
    - With 9th digit:  5531971110477  (13 digits, format: 55 + DDD + 9 + number)
    - Without 9th digit: 553171110477  (12 digits, format: 55 + DDD + number)

    When the bare number is 12 digits and starts with 55 (Brazil), insert '9'
    after the 4-digit country+DDD prefix to reconstruct the standard mobile format.

    Examples:
        553171110477 -> 5531971110477
        5531983456394 -> 5531983456394  (unchanged, already 13 digits)
        5511900000000 -> 5511900000000  (unchanged, no 9 to insert)
    """
    digits = number.strip()
    # Brazilian format: 55 + DDD (2 digits) + number (8 or 9 digits)
    # Valid normalized: 13 digits starting with 55
    if len(digits) == 12 and digits.startswith("55"):
        # Insert '9' after country code (55) + DDD (2 digits) = prefix of 4 digits
        return digits[:4] + "9" + digits[4:]
    return digits


def _is_sender_authorized(sender: str, allowed: list[str]) -> bool:
    """Check if sender is in the allowlist. Empty allowlist blocks all."""
    if not allowed:
        logger.warning("Allowlist is empty — blocking all senders")
        return False
    normalized = _normalize_br_mobile_number(sender)
    if normalized != sender:
        logger.debug(
            "whatsapp-inbound: sender %s normalized to %s for allowlist check",
            sender,
            normalized,
        )
    return normalized in allowed


# ── Pydantic models for webhook payloads ────────────────────────────────────

# Event types that represent inbound messages (vs. connection/qrcode/receipt events)
_MESSAGE_EVENT_TYPES = frozenset([
    "messages.upsert",
    "MESSAGES_UPSERT",
    "message_received",
])


class EvolutionMessage(BaseModel):
    """Minimal model for Evolution API inbound webhook messages (flat format)."""
    key: dict[str, Any] = Field(default_factory=dict)
    message: dict[str, Any] = Field(default_factory=dict)
    pushName: str = ""

    @property
    def msg_id(self) -> str:
        # Evolution sends message ID in key.id; fallback to key.remoteJid stripped
        msg_id = self.key.get("id", "")
        if not msg_id:
            remote_jid = self.key.get("remoteJid", "")
            if remote_jid:
                msg_id = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid
        return msg_id

    @property
    def sender(self) -> str:
        # Evolution sends JID (e.g. "553183456394@s.whatsapp.net" or "54143045632053@lid")
        # Normalize to bare number for allowlist comparison
        remote = self.key.get("remote", "")
        if not remote:
            remote = self.key.get("remoteJid", "")
        # Strip any @ suffix (e.g. @s.whatsapp.net, @lid, @g.us)
        bare = remote.split("@")[0] if remote else ""
        return bare

    @property
    def text(self) -> str:
        msg_data = self.message.get("conversation") or self.message.get("extendedTextMessage", {})
        return msg_data if isinstance(msg_data, str) else msg_data.get("text", "")

    @property
    def from_me(self) -> bool:
        return bool(self.key.get("fromMe", False))


def _parse_message_payload(payload: dict) -> tuple[str, str, str, bool, str, str] | None:
    """
    Extract message data from either flat or enveloped Evolution webhook payload.

    Returns: (msg_id, sender, text, from_me, pushName, event_name) or None if not a message event.
    """
    event = payload.get("event", "")

    # Detect enveloped format: message data inside 'data' field
    data = payload.get("data", {})
    if data:
        # Enveloped format — extract from 'data'
        key_data = data.get("key", {})
        msg_id = data.get("id", "") or key_data.get("id", "")
        if not msg_id:
            remote_jid = key_data.get("remoteJid", "")
            if remote_jid:
                msg_id = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

        remote = key_data.get("remote", "") or key_data.get("remoteJid", "")
        sender = remote.split("@")[0] if remote else ""

        msg_content = data.get("message", {})
        text = msg_content.get("conversation") or msg_content.get("extendedTextMessage", {})
        if isinstance(text, dict):
            text = text.get("text", "") or ""
        text = text or ""

        from_me = bool(key_data.get("fromMe", False))
        push_name = data.get("pushName", "") or ""
        event_name = event
    else:
        # Flat format — payload IS the message
        key_data = payload.get("key", {})
        msg_id = key_data.get("id", "")
        if not msg_id:
            remote_jid = key_data.get("remoteJid", "")
            if remote_jid:
                msg_id = remote_jid.split("@")[0] if "@" in remote_jid else remote_jid

        remote = key_data.get("remote", "") or key_data.get("remoteJid", "")
        sender = remote.split("@")[0] if remote else ""

        msg_content = payload.get("message", {})
        text = msg_content.get("conversation") or msg_content.get("extendedTextMessage", {})
        if isinstance(text, dict):
            text = text.get("text", "") or ""
        text = text or ""

        from_me = bool(key_data.get("fromMe", False))
        push_name = payload.get("pushName", "") or ""
        event_name = ""

    return (msg_id, sender, text, from_me, push_name, event_name)


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/alertmanager")
async def alertmanager_webhook(request: Request) -> dict[str, int]:
    """Existing Alertmanager webhook — must not regress."""
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


@app.post("/whatsapp-inbound")
async def whatsapp_inbound(request: Request, x_evolution_webhook_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """
    Inbound webhook for WhatsApp commands via Evolution API.

    Supports two payload formats:
    - Flat (direct message): {key: {id, remoteJid, fromMe}, message: {conversation}, pushName}
    - Enveloped (from webhook dispatcher): {event, data: {key, message, pushName, ...}}

    Security:
    - Webhook secret header validation
    - Sender allowlist (ALLOWED_WHATSAPP_SENDERS, comma-separated phone numbers)
    - Dedup by message_id (in-memory, non-persistent)
    - Ignores messages sent by the own instance (fromMe=true)
    - Ignores non-message events (connection, qrcode, receipts)
    """
    # ── 1. Authenticate webhook ────────────────────────────────────────────
    expected_secret = os.getenv("EVOLUTION_WEBHOOK_SECRET", "")
    if expected_secret and x_evolution_webhook_secret != expected_secret:
        logger.warning("whatsapp-inbound: invalid webhook secret from %s", request.client.host)
        raise HTTPException(status_code=403, detail="invalid_webhook_secret")

    # ── 2. Parse payload (try enveloped first, then flat) ─────────────────
    payload = await request.json()

    # Try enveloped format first (event + data)
    event_name = payload.get("event", "")
    if event_name and event_name not in _MESSAGE_EVENT_TYPES:
        logger.debug("whatsapp-inbound: ignored event type '%s'", event_name)
        return {"status": "ignored_event", "event": event_name}

    # Extract message data from either format
    parsed = _parse_message_payload(payload)
    if parsed is None:
        logger.warning("whatsapp-inbound: failed to parse payload structure")
        raise HTTPException(status_code=400, detail="invalid_payload")

    msg_id, sender, text, from_me, push_name, event_name = parsed

    logger.info(
        "whatsapp-inbound: msg_id=%s from=%s pushName=%s event=%s text=%r",
        msg_id,
        _mask_number(sender),
        push_name or "(unknown)",
        event_name or "flat",
        text[:80] if text else "",
    )

    # ── 3. Ignore outgoing / self messages ───────────────────────────────
    if from_me:
        logger.debug("whatsapp-inbound: ignoring outgoing message")
        return {"status": "ignored_outgoing"}

    # ── 4. Dedup ──────────────────────────────────────────────────────────
    if msg_id and msg_id in _processed_ids:
        logger.info("whatsapp-inbound: duplicate message %s (masked sender %s)", msg_id, _mask_number(sender))
        return {"status": "duplicate", "message_id": msg_id}
    if msg_id:
        _processed_ids[msg_id] = True
        if len(_processed_ids) > _DEDUP_MAX:
            keys_to_remove = list(_processed_ids)[: len(_processed_ids) // 2]
            for k in keys_to_remove:
                del _processed_ids[k]

    # ── 5. Authorize sender ────────────────────────────────────────────────
    allowlist_raw = os.getenv("ALLOWED_WHATSAPP_SENDERS", "").strip()
    allowed_senders = [s.strip() for s in allowlist_raw.split(",") if s.strip()]

    if not _is_sender_authorized(sender, allowed_senders):
        logger.warning(
            "whatsapp-inbound: unauthorized sender %s (masked %s)",
            sender,
            _mask_number(sender),
        )
        return {"status": "ignored_unauthorized"}

    # ── 6. Parse and dispatch command ─────────────────────────────────────
    if not text:
        return {"status": "ignored_empty"}

    subcmd, action = _parse_command(text)

    if subcmd is None:
        logger.debug("whatsapp-inbound: unrecognised command text %r from %s", text[:40], _mask_number(sender))
        return {"status": "ignored_not_command"}

    handler_entry = _COMMANDS.get("modo", {}).get(subcmd)
    if handler_entry is None:
        response_text = _cmd_help()
    else:
        handler_fn = handler_entry[1]
        if subcmd in ("normal", "gaming"):
            response_text = _cmd_not_ready(subcmd)
        else:
            response_text = handler_fn()

    # ── 7. Send reply via Evolution API ────────────────────────────────────
    ok, reason = _send_whatsapp(response_text, sender)
    if ok:
        logger.info(
            "whatsapp-inbound: command '/modo %s' -> reply sent to %s",
            subcmd,
            _mask_number(sender),
        )
        return {"status": "reply_sent", "command": subcmd, "to": _mask_number(sender)}
    else:
        logger.error(
            "whatsapp-inbound: failed to send reply for '/modo %s' to %s: %s",
            subcmd,
            _mask_number(sender),
            reason,
        )
        return {"status": "reply_failed", "command": subcmd, "reason": reason}