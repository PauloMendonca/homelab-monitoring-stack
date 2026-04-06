import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
from redis import Redis
from redis.exceptions import ResponseError


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("notify-worker")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redis_client() -> Redis:
    return Redis.from_url(os.getenv("REDIS_URL", "redis://redis-notify:6379/0"), decode_responses=True)


def retention_seconds() -> int:
    days = int(os.getenv("STATUS_RETENTION_DAYS", "30"))
    return max(days, 1) * 24 * 60 * 60


def parse_recipients(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
    except json.JSONDecodeError:
        pass
    return []


def send_to_evolution(number: str, text: str) -> tuple[bool, str]:
    base_url = os.getenv("EVOLUTION_BASE_URL", "").rstrip("/")
    instance = os.getenv("EVOLUTION_INSTANCE", "")
    api_key = os.getenv("EVOLUTION_API_KEY", "")
    timeout = int(os.getenv("EVOLUTION_TIMEOUT_SECONDS", "15"))

    if not all([base_url, instance, api_key]):
        return False, "evolution_not_configured"

    url = f"{base_url}/message/sendText/{instance}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    payload = {"number": number, "text": text}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return False, f"request_error:{exc}"

    if response.status_code >= 300:
        return False, f"http_{response.status_code}:{response.text[:180]}"

    return True, "ok"


def trim_streams(db: Redis) -> None:
    now_ms = int(time.time() * 1000)
    ttl_ms = retention_seconds() * 1000
    minid = f"{max(now_ms - ttl_ms, 0)}-0"

    for stream_name in (
        os.getenv("REDIS_STREAM_MESSAGES", "notify:messages"),
        os.getenv("REDIS_STREAM_STATUS", "notify:status"),
        os.getenv("REDIS_STREAM_DLQ", "notify:dlq"),
    ):
        try:
            db.xtrim(stream_name, minid=minid, approximate=True)
        except ResponseError:
            continue


def update_status(db: Redis, message_id: str, status: str, attempt: int, detail: str = "") -> None:
    key = f"notify:message:{message_id}"
    now = utc_now()
    mapping = {
        "status": status,
        "attempt": str(attempt),
        "updated_at": now,
    }
    if detail:
        mapping["detail"] = detail
    db.hset(key, mapping=mapping)
    db.expire(key, retention_seconds())

    status_stream = os.getenv("REDIS_STREAM_STATUS", "notify:status")
    event = {"id": message_id, "status": status, "attempt": str(attempt), "updated_at": now}
    if detail:
        event["detail"] = detail
    db.xadd(status_stream, event)


def enqueue_retry_or_dlq(db: Redis, fields: dict[str, str], attempt: int, reason: str) -> None:
    max_retries = int(os.getenv("MAX_RETRIES", "5"))
    message_id = fields["id"]

    if attempt >= max_retries:
        dlq_stream = os.getenv("REDIS_STREAM_DLQ", "notify:dlq")
        event = dict(fields)
        event["attempt"] = str(attempt)
        event["failed_at"] = utc_now()
        event["reason"] = reason
        db.xadd(dlq_stream, event)
        update_status(db, message_id, "failed", attempt, reason)
        logger.error("Message %s moved to DLQ after %d attempts", message_id, attempt)
        return

    backoff = min(2 ** attempt, 60)
    time.sleep(backoff)
    retry_event = dict(fields)
    retry_event["attempt"] = str(attempt)
    retry_event["requeued_at"] = utc_now()
    db.xadd(os.getenv("REDIS_STREAM_MESSAGES", "notify:messages"), retry_event)
    update_status(db, message_id, "retrying", attempt, reason)
    logger.warning("Message %s requeued (attempt %d): %s", message_id, attempt, reason)


def process_event(db: Redis, fields: dict[str, str]) -> None:
    message_id = fields.get("id", "")
    text = fields.get("text", "")
    recipients = parse_recipients(fields.get("recipients", "[]"))
    attempt = int(fields.get("attempt", "0")) + 1

    if not message_id or not text or not recipients:
        logger.error("Invalid message payload, skipping")
        return

    errors = []
    for number in recipients:
        ok, reason = send_to_evolution(number, text)
        if not ok:
            errors.append(f"{number}:{reason}")

    if errors:
        enqueue_retry_or_dlq(db, fields, attempt, ";".join(errors))
        return

    update_status(db, message_id, "sent", attempt)
    logger.info("Message %s sent to %d recipients", message_id, len(recipients))


def ensure_group(db: Redis, stream: str, group: str) -> None:
    try:
        db.xgroup_create(stream, group, id="0", mkstream=True)
        logger.info("Consumer group created: %s on %s", group, stream)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def main() -> None:
    db = redis_client()
    stream = os.getenv("REDIS_STREAM_MESSAGES", "notify:messages")
    group = os.getenv("REDIS_CONSUMER_GROUP", "notify-workers")
    consumer = os.getenv("REDIS_CONSUMER_NAME", f"worker-{os.getpid()}")
    block_ms = int(os.getenv("REDIS_BLOCK_MS", "5000"))

    ensure_group(db, stream, group)
    last_trim = 0.0

    while True:
        events = db.xreadgroup(group, consumer, {stream: ">"}, count=10, block=block_ms)
        if not events:
            now = time.time()
            if now - last_trim > 3600:
                trim_streams(db)
                last_trim = now
            continue

        for _, records in events:
            for record_id, fields in records:
                try:
                    process_event(db, fields)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Worker failed for record %s: %s", record_id, exc)
                finally:
                    db.xack(stream, group, record_id)


if __name__ == "__main__":
    main()
