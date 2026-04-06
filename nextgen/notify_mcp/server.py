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


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    mcp.run(transport=transport)
