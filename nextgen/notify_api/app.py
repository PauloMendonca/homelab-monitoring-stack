import json
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from pydantic import field_validator
from redis import Redis
import httpx


# ---------------------------------------------------------------------------
# Logging — no secrets
# ---------------------------------------------------------------------------
logger = logging.getLogger("notify-api")
# Quiet down third-party loggers
for _lib in ("uvicorn", "uvicorn.access", "httpx"):
    logging.getLogger(_lib).setLevel(logging.WARNING)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis() -> Redis:
    return Redis.from_url(os.getenv("REDIS_URL", "redis://redis-notify:6379/0"), decode_responses=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _require_api_key(x_api_key: str | None) -> None:
    """Legacy WhatsApp endpoint auth — fails open when NOTIFY_API_KEY is unset."""
    expected = os.getenv("NOTIFY_API_KEY", "")
    if not expected:
        # Legacy behaviour: allow requests when key is not configured.
        # K8s deployments that set NOTIFY_API_KEY will enforce it.
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _check_bearer_token(request: Request) -> tuple[int, dict[str, Any]] | None:
    """
    Strict Bearer-token auth for /tools/* endpoints.
    Returns None on success.
    Returns (401, {ok,error}) when client token is missing/malformed/wrong.
    Returns (503, {ok,error}) when NOTIFY_TOOL_API_KEY is not set (backend not configured).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return 401, _safe_error("invalid_authorization_header")
    token = auth[7:]  # strip "Bearer "
    if not token:
        return 401, _safe_error("missing_token")
    expected = os.getenv("NOTIFY_TOOL_API_KEY", "")
    if not expected:
        # Tool endpoint is not configured; reject all requests.
        logger.warning("/tools/notify-paulo called but NOTIFY_TOOL_API_KEY is not set — rejecting")
        return 503, _safe_error("endpoint_not_configured")
    if token != expected:
        return 401, _safe_error("invalid_token")
    return None


# ---------------------------------------------------------------------------
# Rate limiting — in-memory per replica
# ---------------------------------------------------------------------------
class _SourceRateLimiter:
    """
    Simple in-memory sliding-window rate limiter keyed by `source`.
    Caveat: each pod replica has its own counter; this is intentionally
    lightweight and not consistent across replicas.
    """
    def __init__(self, per_minute: int = 5, per_hour: int = 30) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        # {source: [(ts, count), ...]} — timestamps of the sliding window
        self._minute: dict[str, list[float]] = defaultdict(list)
        self._hour: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def _clean(self, timestamps: list[float], cutoff: float) -> None:
        # Remove entries older than cutoff (in-place)
        # Binary search would be faster for large N; for these small limits list scan is fine
        i = 0
        n = len(timestamps)
        while i < n and timestamps[i] < cutoff:
            i += 1
        if i > 0:
            del timestamps[:i]

    def check(self, source: str) -> tuple[bool, str]:
        """
        Returns (allowed, reason). reason is non-empty when allowed is False.
        """
        import time
        now = time.time()
        minute_cutoff = now - 60
        hour_cutoff = now - 3600

        with self._lock:
            m = self._minute[source]
            h = self._hour[source]
            self._clean(m, minute_cutoff)
            self._clean(h, hour_cutoff)

            if len(m) >= self.per_minute:
                return False, f"rate_limit_per_source: {self.per_minute}/minute exceeded for '{source}'"
            if len(h) >= self.per_hour:
                return False, f"rate_limit_per_source: {self.per_hour}/hour exceeded for '{source}'"

            m.append(now)
            h.append(now)
            return True, ""


_rate_limiter = _SourceRateLimiter()


# ---------------------------------------------------------------------------
# Deduplication — in-memory, ~10-minute window
# ---------------------------------------------------------------------------
_dedupe: dict[str, float] = {}
_dedupe_lock = Lock()
_DEDUPE_TTL_SECONDS = 600


def _is_dupe(key: str) -> bool:
    import time
    now = time.time()
    with _dedupe_lock:
        if key in _dedupe:
            if _dedupe[key] > now:
                return True
            del _dedupe[key]
        _dedupe[key] = now + _DEDUPE_TTL_SECONDS
        return False


# ---------------------------------------------------------------------------
# Slack client
# ---------------------------------------------------------------------------
_SLACK_API_URL = "https://slack.com/api/chat.postMessage"


def _build_slack_blocks(title: str, message: str, severity: str, source: str, metadata: dict[str, Any] | None) -> list[dict]:
    """
    Build Slack Block Kit message.
    metadata is already validated and sanitised by the Pydantic validator
    (via _validate_and_sanitize_metadata) before reaching here, so it is
    guaranteed to be a flat dict with simple values.
    """
    emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(severity, "ℹ️")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {title}"[:120], "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Source:*\n{source}"},
                {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": message},
        },
    ]
    if metadata:
        import json
        meta_str = json.dumps(metadata, separators=(",", ":"))
        if len(meta_str) > 500:
            meta_str = meta_str[:500] + "...[truncated]"
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Metadata:*\n```json\n{meta_str}\n```"},
        })
    blocks.append({"type": "divider"})
    return blocks


async def _slack_post(token: str, channel: str, blocks: list[dict]) -> tuple[bool, str]:
    """
    Calls Slack chat.postMessage. Returns (ok, slack_ts_or_error).
    Does NOT log the token.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                _SLACK_API_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"channel": channel, "blocks": blocks, "text": "notification"},
            )
        except httpx.RequestError as exc:
            return False, f"slack_network_error: {exc}"
        try:
            data = resp.json()
        except Exception:
            return False, f"slack_invalid_response: {resp.text[:200]}"

        if not data.get("ok"):
            # Surface the error without leaking token
            slack_error = data.get("error", "unknown_error")
            logger.warning("Slack API error: %s", slack_error)
            return False, f"slack_error: {slack_error}"

        ts = data.get("ts", "")
        return True, ts


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
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


class NotifyPauloRequest(BaseModel):
    """
    Strict request model for /tools/notify-paulo.
    - Unknown fields are rejected (extra = "forbid").
    - metadata must be a flat object with simple values (str/int/float/bool/null).
    - Secret keys are redacted by the validator before storage.
    """
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=3000)
    severity: str = Field(default="info")
    source: str = Field(default="system")
    dedupe_key: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] | None = Field(default=None)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity_vals(cls, v: str | None) -> str:
        if v is None:
            return "info"
        if not isinstance(v, str):
            raise ValueError("severity must be a string")
        allowed = {"info", "warning", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"severity must be one of {sorted(allowed)}")
        return v.lower()

    @field_validator("source", mode="before")
    @classmethod
    def _source_vals(cls, v: str | None) -> str:
        if v is None:
            return "system"
        if not isinstance(v, str):
            raise ValueError("source must be a string")
        allowed = {"hermes-memory-agent", "maxhermes-agent", "coding-agent", "system"}
        if v.lower() not in allowed:
            raise ValueError(f"source must be one of {sorted(allowed)}")
        return v.lower()

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_sanitize(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Reject or sanitise metadata before the model is constructed."""
        if v is None:
            return None
        if not isinstance(v, dict):
            raise ValueError("metadata must be an object")
        # Raises ValueError on invalid structure; FastAPI converts to 422
        return _validate_and_sanitize_metadata(v)


# ---------------------------------------------------------------------------
# Strict metadata validation — flat, simple values only, no secrets
# ---------------------------------------------------------------------------
#: Maximum number of metadata key-value pairs
MAX_METADATA_PAIRS = 20
#: Maximum byte-size of the serialised metadata JSON string
MAX_METADATA_SIZE_BYTES = 1024
#: Maximum length of a metadata value string
MAX_METADATA_VALUE_LEN = 200

#: Secret key substrings (case-insensitive). Matched as substring on the lowercased key.
_METADATA_SECRET_KEYWORDS = frozenset({
    "token", "password", "secret", "key", "authorization", "bearer",
    "cookie", "session", "xoxb", "xoxp", "auth", "credential", "passwd",
    "private", "apikey", "api_key", "api-key", "access_token", "refresh_token",
    "client_secret",
})

#: Compiled regex for secret-like string values. Matches Bearer tokens, Slack tokens,
#: OpenAI keys, 1Password refs, and generic credential patterns.
_SECRET_VALUE_RE = re.compile(
    r"(?i)"
    r"(xox[bpr]-[a-zA-Z0-9\-]{10,}|"      # Slack xoxb-/xoxp-/xoxr- tokens
    r"bearer\s+[a-zA-Z0-9_\-]{5,}|"        # Bearer token
    r"sk-[a-zA-Z0-9]{20,}|"                # OpenAI sk- keys
    r"op://[a-zA-Z0-9/\-_]+|"             # 1Password op:// refs
    r"token[=:]\s*['\"]?[a-zA-Z0-9_\-]{8,}['\"]?|"
    r"password[=:]\s*['\"]?[^\s'\"]{6,}['\"]?|"
    r"[a-zA-Z0-9+/]{40,}={0,2})",           # Base64-encoded credentials (40+ chars)
    re.IGNORECASE,
)


def _is_secret_key(key: str) -> bool:
    """Return True if key (case-insensitive) contains a secret keyword."""
    k_lower = key.lower()
    return any(kw in k_lower for kw in _METADATA_SECRET_KEYWORDS)


def _is_secret_value(value: str) -> bool:
    """Return True if string value looks like a credential or secret."""
    if not isinstance(value, str):
        return False
    return bool(_SECRET_VALUE_RE.search(value))


def _validate_and_sanitize_metadata(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Validate and sanitise a metadata dict for /tools/notify-paulo.
    - Rejects non-dict types (lists, nested dicts, etc.)
    - Redacts secret-looking keys and secret-looking string values.
    - Caps total size.
    Raises ValueError on validation failure (caught by FastAPI).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("metadata must be an object")

    if len(raw) > MAX_METADATA_PAIRS:
        raise ValueError(
            f"metadata exceeds maximum of {MAX_METADATA_PAIRS} key-value pairs"
        )

    import json
    result: dict[str, Any] = {}
    for k, v in raw.items():
        if _is_secret_key(k):
            result[k] = "[redacted]"
            continue
        if not isinstance(v, (str, int, float, bool, type(None))):
            raise ValueError(
                f"metadata values must be simple types (str/int/float/bool/null); "
                f"got {type(v).__name__} at key {k!r}"
            )
        if isinstance(v, str) and len(v) > MAX_METADATA_VALUE_LEN:
            v = v[:MAX_METADATA_VALUE_LEN] + "[truncated]"
        # Redact secret-looking string values even under non-secret keys
        if isinstance(v, str) and _is_secret_value(v):
            result[k] = "[redacted]"
            continue
        result[k] = v

    encoded = json.dumps(result, separators=(",", ":"))
    if len(encoded.encode()) > MAX_METADATA_SIZE_BYTES:
        raise ValueError("metadata object exceeds maximum size")

    return result


app = FastAPI(title="notify-api", version="0.3.0")  # version bump for /tools endpoint


# ---------------------------------------------------------------------------
# Custom exception handler — convert FastAPI validation errors to our {ok, error} shape
# ---------------------------------------------------------------------------
from fastapi.exceptions import RequestValidationError


#: Compiled regex for credential patterns that must never appear in error responses.
_ERROR_CREDENTIAL_RE = re.compile(
    r"(?i)"
    r"(xox[bpr]-[a-zA-Z0-9-]+|"
    r"bearer\s+[a-zA-Z0-9_\-]+|"
    r"op://[^,\s\"}\]]+|"
    r"sk-[a-zA-Z0-9]{20,}|"
    r"token[=:]\s*[\"']?[a-zA-Z0-9_\-]+[\"']?)",
    re.IGNORECASE,
)
_MAX_ERROR_LEN = 120


def _safe_error(detail: str) -> dict[str, Any]:
    """
    Build a safe error response from an internal detail string.
    - Caps length to _MAX_ERROR_LEN characters.
    - Redacts credential-like strings (Slack tokens, Bearer tokens, op:// refs, etc.).
    - Never returns raw detail if it contains credential patterns.
    """
    # Strip and cap
    msg = str(detail).strip()
    if len(msg) > _MAX_ERROR_LEN:
        msg = msg[:_MAX_ERROR_LEN].rstrip() + "...[truncated]"

    # If the message contains a credential pattern, discard it — do not partial-redact
    if _ERROR_CREDENTIAL_RE.search(msg):
        msg = "request_error"

    return {"ok": False, "error": msg}


@app.exception_handler(RequestValidationError)
def _validation_exception_handler(request: Request, exc: RequestValidationError) -> Any:
    """
    Convert FastAPI's RequestValidationError to the required {ok, error} shape.
    Used for /tools/notify-paulo input validation (unknown fields, type errors, etc.).
    """
    errors = exc.errors()
    if not errors:
        return JSONResponse(status_code=422, content=_safe_error("validation_error"))
    first = errors[0]
    loc = ".".join(str(l) for l in first.get("loc", []))
    msg = first.get("msg", "validation_error")
    if loc:
        detail = f"{loc}: {msg}"
    else:
        detail = msg
    return JSONResponse(status_code=422, content=_safe_error(detail))


def _retention_seconds() -> int:
    days = int(os.getenv("STATUS_RETENTION_DAYS", "30"))
    return max(days, 1) * 24 * 60 * 60


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


# ---------------------------------------------------------------------------
# /tools/notify-paulo — Slack DM to Paulo via Bearer auth
# ---------------------------------------------------------------------------
@app.post("/tools/notify-paulo", name="notify_paulo")
async def notify_paulo(
    request: Request,
    payload: NotifyPauloRequest,
) -> dict[str, Any]:
    """
    Send a Slack DM to Paulo.

    Auth: Authorization: Bearer <NOTIFY_TOOL_API_KEY>
    Fixed destination: SLACK_PAULO_USER_ID from env (never user-controlled).
    Slack token: SLACK_BOT_TOKEN from env (never exposed to callers).

    Success response:
      200 { "ok": true, "slack_ts": "...", "destination": "paulo_dm" }
      200 { "ok": true, "slack_ts": null, "destination": "paulo_dm", "deduped": true }

    Error response (all cases):
      4xx/5xx { "ok": false, "error": "short safe message" }
    """
    # ── Auth ────────────────────────────────────────────────────────────────
    auth_result = _check_bearer_token(request)
    if auth_result is not None:
        status_code, body = auth_result
        return JSONResponse(status_code=status_code, content=body)

    # ── Rate limit ─────────────────────────────────────────────────────────
    allowed, reason = _rate_limiter.check(payload.source)
    if not allowed:
        return JSONResponse(status_code=429, content=_safe_error(reason))

    # ── Dedupe ─────────────────────────────────────────────────────────────
    if payload.dedupe_key and _is_dupe(payload.dedupe_key):
        logger.info("notify_paulo deduped key=%r source=%r", payload.dedupe_key, payload.source)
        return {"ok": True, "slack_ts": None, "destination": "paulo_dm", "deduped": True}

    # ── Build Slack blocks ──────────────────────────────────────────────────
    blocks = _build_slack_blocks(
        title=payload.title,
        message=payload.message,
        severity=payload.severity,
        source=payload.source,
        metadata=payload.metadata,
    )

    # ── Fixed config from env ──────────────────────────────────────────────
    slack_token = os.getenv("SLACK_BOT_TOKEN", "")
    paulo_user_id = os.getenv("SLACK_PAULO_USER_ID", "")

    if not slack_token or not paulo_user_id:
        logger.warning("notify_paulo called but SLACK_BOT_TOKEN or SLACK_PAULO_USER_ID not set")
        return JSONResponse(status_code=503, content=_safe_error("endpoint_not_configured"))

    # ── Dry-run (placeholder token — no real Slack call) ────────────────────
    if slack_token in ("xoxb-test-placeholder", "xoxb-xxxxxxx"):
        logger.info("notify_paulo dry-run (placeholder token) title=%r", payload.title)
        return {"ok": True, "slack_ts": "dry_run.12345", "destination": "paulo_dm"}

    # ── Real Slack call ────────────────────────────────────────────────────
    ok, result = await _slack_post(slack_token, paulo_user_id, blocks)

    if not ok:
        error_msg = result if result.startswith("slack_network_error") else result
        logger.warning("notify_paulo Slack error: %s — title=%r", error_msg, payload.title)
        return JSONResponse(status_code=502, content=_safe_error(error_msg))

    logger.info("notify_paulo sent ok ts=%s title=%r source=%r", result, payload.title, payload.source)
    return {"ok": True, "slack_ts": result, "destination": "paulo_dm"}
