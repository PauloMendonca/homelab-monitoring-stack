import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from redis import Redis


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis() -> Redis:
    return Redis.from_url(os.getenv("REDIS_URL", "redis://redis-notify:6379/0"), decode_responses=True)


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("NOTIFY_API_KEY", "")
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _retention_seconds() -> int:
    days = int(os.getenv("STATUS_RETENTION_DAYS", "30"))
    return max(days, 1) * 24 * 60 * 60


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    recipients: list[str] = Field(min_length=1)
    source: str = Field(default="unknown", max_length=128)
    priority: str = Field(default="normal", max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    source: str = Field(default="unknown", max_length=128)
    priority: str = Field(default="normal", max_length=32)
    policy: str = Field(default="general", max_length=64)
    recipients: list[str] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="notify-api", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/messages")
def enqueue_message(payload: MessageRequest, x_api_key: str | None = Header(default=None)) -> dict[str, str]:
    _require_api_key(x_api_key)
    db = _redis()

    message_id = str(uuid.uuid4())
    now = _utc_now()

    msg_key = f"notify:message:{message_id}"
    db.hset(
        msg_key,
        mapping={
            "id": message_id,
            "status": "queued",
            "source": payload.source,
            "priority": payload.priority,
            "text": payload.text,
            "recipients": json.dumps(payload.recipients),
            "metadata": json.dumps(payload.metadata),
            "attempt": "0",
            "created_at": now,
            "updated_at": now,
        },
    )
    db.expire(msg_key, _retention_seconds())

    stream = os.getenv("REDIS_STREAM_MESSAGES", "notify:messages")
    db.xadd(
        stream,
        {
            "id": message_id,
            "text": payload.text,
            "source": payload.source,
            "priority": payload.priority,
            "recipients": json.dumps(payload.recipients),
            "metadata": json.dumps(payload.metadata),
            "attempt": "0",
            "created_at": now,
        },
    )

    status_stream = os.getenv("REDIS_STREAM_STATUS", "notify:status")
    db.xadd(
        status_stream,
        {
            "id": message_id,
            "status": "queued",
            "created_at": now,
            "source": payload.source,
        },
    )

    return {"message_id": message_id, "status": "queued"}


@app.post("/v1/messages/policy")
def enqueue_policy_message(
    payload: PolicyMessageRequest, x_api_key: str | None = Header(default=None)
) -> dict[str, str]:
    request_payload = MessageRequest(
        text=payload.text,
        recipients=payload.recipients,
        source=payload.source,
        priority=payload.priority,
        metadata={**payload.metadata, "policy": payload.policy},
    )
    return enqueue_message(request_payload, x_api_key)


@app.get("/v1/messages/{message_id}")
def get_message_status(message_id: str, x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    _require_api_key(x_api_key)
    db = _redis()
    data = db.hgetall(f"notify:message:{message_id}")
    if not data:
        raise HTTPException(status_code=404, detail="message_not_found")

    for key in ("recipients", "metadata"):
        if key in data:
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data
