import os
from typing import Any

import requests
from mcp.server.fastmcp import FastMCP


_host = os.getenv("MCP_HOST", "0.0.0.0")
_port = int(os.getenv("MCP_PORT", "8000"))

mcp = FastMCP("notify-mcp", host=_host, port=_port)


def _tool_description(name: str, description: str) -> str:
    text = description.strip()
    if text:
        return text
    return f"Tool: {name}"


def _api_base() -> str:
    return os.getenv("NOTIFY_API_URL", "http://notify-api.ai-platform.svc.cluster.local:8080").rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("NOTIFY_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(f"{_api_base()}{path}", headers=_headers(), json=payload, timeout=15)
    if response.status_code >= 300:
        return {"ok": False, "status": response.status_code, "body": response.text[:400]}
    return {"ok": True, "data": response.json()}


def _tool_headers() -> dict[str, str]:
    """
    Headers for calling the /tools/notify-paulo endpoint.
    Uses NOTIFY_TOOL_API_KEY (separate from NOTIFY_API_KEY used for WhatsApp).
    NEVER exposes Slack credentials to MCP callers.
    """
    headers = {"Content-Type": "application/json"}
    tool_key = os.getenv("NOTIFY_TOOL_API_KEY", "")
    if tool_key:
        headers["Authorization"] = f"Bearer {tool_key}"
    return headers


def _tool_post(payload: dict[str, Any]) -> dict[str, Any]:
    """
    POST to /tools/notify-paulo on notify-api.
    The NOTIFY_TOOL_API_KEY is injected here by the MCP server (backend-side),
    so Hermes/MaxHermes never sees a Slack token or the tool API key.
    """
    response = requests.post(
        f"{_api_base()}/tools/notify-paulo",
        headers=_tool_headers(),
        json=payload,
        timeout=20,
    )
    if response.status_code >= 300:
        return {
            "ok": False,
            "status": response.status_code,
            "error": response.json().get("detail", response.text[:200]) if response.content else response.text[:200],
        }
    data = response.json()
    return {"ok": True, "data": data}


@mcp.tool(description=_tool_description("send_message", "Send one WhatsApp message through notify-api."))
def send_message(number: str, text: str, source: str = "agent", priority: str = "normal") -> dict[str, Any]:
    return _post(
        "/v1/messages",
        {
            "text": text,
            "recipients": [number],
            "source": source,
            "priority": priority,
            "metadata": {"channel": "whatsapp"},
        },
    )


@mcp.tool(description=_tool_description("send_bulk", "Send one WhatsApp message to multiple recipients through notify-api."))
def send_bulk(numbers: list[str], text: str, source: str = "agent", priority: str = "normal") -> dict[str, Any]:
    clean = [num for num in numbers if num]
    if not clean:
        return {"ok": False, "error": "no_numbers_provided"}
    return _post(
        "/v1/messages",
        {
            "text": text,
            "recipients": clean,
            "source": source,
            "priority": priority,
            "metadata": {"channel": "whatsapp"},
        },
    )


@mcp.tool(description=_tool_description("delivery_status", "Check delivery status for a queued WhatsApp message by id."))
def delivery_status(message_id: str) -> dict[str, Any]:
    response = requests.get(f"{_api_base()}/v1/messages/{message_id}", headers=_headers(), timeout=10)
    if response.status_code >= 300:
        return {"ok": False, "status": response.status_code, "body": response.text[:400]}
    return {"ok": True, "data": response.json()}


@mcp.tool(
    description=_tool_description(
        "notify_paulo",
        (
            "Send a Slack DM to Paulo. "
            "title: max 120 chars. "
            "message: max 3000 chars. "
            "severity: info | warning | critical (default: info). "
            "source: hermes-memory-agent | maxhermes-agent | coding-agent | system (default: system). "
            "dedupe_key: optional — deduplicate within 10 minutes. "
            "metadata: optional flat dict (secret values are redacted server-side). "
            "Returns {ok, slack_ts, destination} on success."
        ),
    )
)
def notify_paulo(
    title: str,
    message: str,
    severity: str = "info",
    source: str = "system",
    dedupe_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send a Slack DM to Paulo via the notify-api /tools/notify-paulo endpoint.

    This tool does NOT require or accept Slack credentials.
    Auth is handled by NOTIFY_TOOL_API_KEY injected into the backend.
    Slack token and Paulo's user ID are held exclusively by notify-api.
    """
    payload: dict[str, Any] = {
        "title": title,
        "message": message,
        "severity": severity,
        "source": source,
    }
    if dedupe_key is not None:
        payload["dedupe_key"] = dedupe_key
    if metadata is not None:
        payload["metadata"] = metadata
    return _tool_post(payload)


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    mcp.run(transport=transport)
