# Tests for notify-api /tools/notify-paulo endpoint
# Run with: pytest tests/test_notify_paulo.py -v
#
# These tests mock the Slack API and Redis, and do NOT make real Slack calls.
#
# NOTE: FastAPI's TestClient processes async handlers synchronously for
# sync endpoints. The /tools/notify-paulo endpoint is async but TestClient
# handles it correctly. We avoid complex mock-patching for async closures
# by testing BEHAVIOUR (HTTP status codes, response bodies) rather than
# asserting on internal mock call counts.

import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the notify_api package is importable
notify_api_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, notify_api_path)

# Import the app module eagerly so _rate_limiter is initialised before
# autouse reset fixtures run.
import notify_api.app as _notify_api_app  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset in-memory rate limiter and dedupe state before each test."""
    rl = getattr(_notify_api_app, "_rate_limiter", None)
    if rl is not None:
        with rl._lock:
            rl._minute.clear()
            rl._hour.clear()
    dd = getattr(_notify_api_app, "_dedupe", None)
    if dd is not None:
        with _notify_api_app._dedupe_lock:
            dd.clear()
    yield


@pytest.fixture
def app():
    """Provide the notify-api app with patched env vars for tool endpoint tests."""
    orig_env = {k: os.environ.get(k) for k in (
        "NOTIFY_TOOL_API_KEY", "SLACK_BOT_TOKEN", "SLACK_PAULO_USER_ID",
        "REDIS_URL", "REDIS_STREAM_MESSAGES", "REDIS_STREAM_STATUS", "STATUS_RETENTION_DAYS",
    )}
    try:
        os.environ.update({
            "NOTIFY_TOOL_API_KEY": "test-tool-key-12345",
            "SLACK_BOT_TOKEN": "xoxb-test-placeholder",  # dry-run mode
            "SLACK_PAULO_USER_ID": "UXXXXXXXXX",
            "REDIS_URL": "redis://localhost:6379/15",
            "REDIS_STREAM_MESSAGES": "notify:messages",
            "REDIS_STREAM_STATUS": "notify:status",
            "STATUS_RETENTION_DAYS": "1",
        })
        with patch.object(_notify_api_app, "_redis") as mock_redis:
            mock_redis_instance = MagicMock()
            mock_redis.return_value = mock_redis_instance
            yield _notify_api_app.app
    finally:
        for k, v in orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-tool-key-12345"}


@pytest.fixture
def invalid_auth_headers():
    return {"Authorization": "Bearer wrong-key"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_body(resp) -> dict:
    """Parse response JSON assuming {ok: false, error: ...} shape."""
    return resp.json()


# ---------------------------------------------------------------------------
# Auth tests — all return {ok: false, error: "..."}
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_auth_header_returns_401_with_ok_false_error(self, client):
        """No Authorization header → 401 {ok: false, error: ...}"""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
        )
        assert resp.status_code == 401
        body = _error_body(resp)
        assert body["ok"] is False
        assert "error" in body
        assert "detail" not in body  # no FastAPI detail leak

    def test_bearer_prefix_required(self, client):
        """Authorization without 'Bearer ' prefix → 401 {ok: false, error}"""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers={"Authorization": "test-tool-key-12345"},
        )
        assert resp.status_code == 401
        body = _error_body(resp)
        assert body["ok"] is False
        assert "error" in body

    def test_wrong_token_returns_401_with_ok_false_error(self, client, invalid_auth_headers):
        """Wrong Bearer token → 401 {ok: false, error: invalid_token}"""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers=invalid_auth_headers,
        )
        assert resp.status_code == 401
        body = _error_body(resp)
        assert body["ok"] is False
        assert body["error"] == "invalid_token"
        assert "detail" not in str(body)

    def test_token_not_in_response_body(self, client, auth_headers):
        """Bearer token value must not appear anywhere in the response."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body_str = str(resp.json()).lower()
        # Token value itself must not appear
        assert "test-tool-key-12345" not in body_str
        assert "xoxb" not in body_str
        assert "slack_bot_token" not in body_str
        assert "slack_paulo_user_id" not in body_str


# ---------------------------------------------------------------------------
# Payload strictness — unknown fields rejected with {ok: false, error}
# ---------------------------------------------------------------------------

class TestPayloadStrictness:
    def test_unknown_field_api_key_rejected(self, client, auth_headers):
        """api_key in body must be rejected (extra=forbid)."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "api_key": "some-key"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False
        assert "error" in body
        assert "detail" not in str(body)

    def test_unknown_field_channel_rejected(self, client, auth_headers):
        """channel in body must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "channel": "#alerts"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_unknown_field_user_id_rejected(self, client, auth_headers):
        """user_id in body must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "user_id": "UXXXX"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_unknown_field_token_rejected(self, client, auth_headers):
        """token in body must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "token": "sk-xxx"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_unknown_field_slack_token_rejected(self, client, auth_headers):
        """slack_token in body must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "slack_token": "xoxb-xxx"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_unknown_field_destination_rejected(self, client, auth_headers):
        """destination in body must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "destination": "some_channel"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_query_string_api_key_ignored(self, client, auth_headers):
        """api_key in query string is ignored (not a declared parameter)."""
        # Without Bearer auth, should still get 401 (auth required)
        resp = client.post(
            "/tools/notify-paulo?api_key=some-key",
            json={"title": "test", "message": "hello"},
        )
        # FastAPI ignores query params not declared in the endpoint signature,
        # so the request goes through without Bearer → 401
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------

class TestPayloadValidation:
    def test_title_required(self, client, auth_headers):
        resp = client.post("/tools/notify-paulo", json={"message": "hello"}, headers=auth_headers)
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False
        assert "error" in body

    def test_message_required(self, client, auth_headers):
        resp = client.post("/tools/notify-paulo", json={"title": "hello"}, headers=auth_headers)
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_title_max_120_chars(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "x" * 121, "message": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_title_exactly_120_ok(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "x" * 120, "message": "hello"},
            headers=auth_headers,
        )
        # dry-run: placeholder token → 200
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["slack_ts"] == "dry_run.12345"

    def test_message_max_3000_chars(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "x" * 3001},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_severity_valid_values(self, client, auth_headers):
        for severity in ("info", "warning", "critical"):
            resp = client.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": "hello", "severity": severity},
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"severity={severity} should be 200"

    def test_severity_invalid_rejected(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "severity": "high"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_source_valid_values(self, client, auth_headers):
        for source in ("hermes-memory-agent", "maxhermes-agent", "coding-agent", "system"):
            resp = client.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": "hello", "source": source},
                headers=auth_headers,
            )
            assert resp.status_code == 200, f"source={source} should be 200"

    def test_source_invalid_rejected(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "source": "unknown-agent"},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_dedupe_key_optional_accepted(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "dedupe_key": "my-key-123"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_metadata_optional_accepted_with_simple_values(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={
                "title": "test",
                "message": "hello",
                "metadata": {"component": "ollama", "gpu": "A4000", "count": 2, "active": True},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Metadata strictness
# ---------------------------------------------------------------------------

class TestMetadataStrictness:
    def test_metadata_nested_object_rejected(self, client, auth_headers):
        """Nested dict in metadata must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={
                "title": "test",
                "message": "hello",
                "metadata": {"outer": {"inner": "value"}},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False
        assert "error" in body

    def test_metadata_list_value_rejected(self, client, auth_headers):
        """List in metadata must be rejected."""
        resp = client.post(
            "/tools/notify-paulo",
            json={
                "title": "test",
                "message": "hello",
                "metadata": {"items": ["a", "b", "c"]},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False

    def test_metadata_secret_keys_redacted(self):
        """Secret-looking keys are redacted."""
        from notify_api.app import _validate_and_sanitize_metadata
        raw = {
            "component": "ollama",
            "token": "sk-12345",
            "authorization": "Bearer xyz",
            "api_key": "secret",
            "password": "hunter2",
            "session": "abc123",
            "xoxb": "xoxb-something",
        }
        result = _validate_and_sanitize_metadata(raw)
        assert result["token"] == "[redacted]"
        assert result["authorization"] == "[redacted]"
        assert result["api_key"] == "[redacted]"
        assert result["password"] == "[redacted]"
        assert result["session"] == "[redacted]"
        assert result["xoxb"] == "[redacted]"
        assert result["component"] == "ollama"

    def test_metadata_secret_values_redacted(self):
        """Secret-looking string values are redacted, even under safe-looking keys."""
        from notify_api.app import _validate_and_sanitize_metadata
        raw = {
            "component": "ollama",
            "endpoint": "Bearer eyJhbGc...",
            "slack_token": "xoxb-xoxb-xoxb-xoxb-xoxb-xoxb-xoxb-xoxb",
            "op_ref": "op://vault/item/field",
            "api_secret": "sk-OpenAISecretKeyValue012345",
            "host": "server1",          # safe value — should pass through
            "count": 42,                # int — should pass through
        }
        result = _validate_and_sanitize_metadata(raw)
        assert result["endpoint"] == "[redacted]"
        assert result["slack_token"] == "[redacted]"
        assert result["op_ref"] == "[redacted]"
        assert result["api_secret"] == "[redacted]"
        assert result["component"] == "ollama"
        assert result["host"] == "server1"
        assert result["count"] == 42

    def test_metadata_safe_value_not_redacted(self):
        """Normal string values that don't match credential patterns are preserved."""
        from notify_api.app import _validate_and_sanitize_metadata
        raw = {
            "service": "ollama",
            "gpu": "NVIDIA A4000",
            "memory_used_mb": 8192,
            "active": True,
        }
        result = _validate_and_sanitize_metadata(raw)
        assert result == raw

    def test_metadata_nested_object_raises_value_error(self):
        """Nested dict in metadata must raise ValueError (caught by FastAPI → 422)."""
        from notify_api.app import _validate_and_sanitize_metadata
        raw = {"outer": {"inner": "value"}}
        with pytest.raises(ValueError, match="simple types"):
            _validate_and_sanitize_metadata(raw)

    def test_metadata_none_input_returns_none(self):
        from notify_api.app import _validate_and_sanitize_metadata
        assert _validate_and_sanitize_metadata(None) is None

    def test_metadata_too_many_pairs_rejected(self, client, auth_headers):
        """More than 20 key-value pairs must be rejected."""
        big_meta = {f"key{i}": f"val{i}" for i in range(21)}
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "metadata": big_meta},
            headers=auth_headers,
        )
        assert resp.status_code == 422
        body = _error_body(resp)
        assert body["ok"] is False


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class TestResponseSchema:
    def test_success_response_fields(self, client, auth_headers):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "slack_ts" in body
        assert body["destination"] == "paulo_dm"

    def test_dedupe_second_call_returns_deduped_flag(self, client, auth_headers):
        """Second call with same dedupe_key returns {ok: true, deduped: true}."""
        # First call
        resp1 = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "dedupe_key": "shared-key"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200
        assert resp1.json().get("deduped") is None

        # Second call with same key
        resp2 = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "dedupe_key": "shared-key"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200
        assert resp2.json().get("deduped") is True
        assert resp2.json().get("slack_ts") is None  # no Slack call made


# ---------------------------------------------------------------------------
# Rate limiting — returns {ok: false, error: ...}
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_rate_limit_returns_429_with_ok_false_error(self, client, auth_headers):
        """6th request in a minute for 'system' source → 429 {ok: false, error}"""
        payload = {"title": "test", "message": "hello", "source": "system"}
        for i in range(5):
            resp = client.post("/tools/notify-paulo", json=payload, headers=auth_headers)
            assert resp.status_code == 200, f"request {i+1} should succeed"

        resp = client.post("/tools/notify-paulo", json=payload, headers=auth_headers)
        assert resp.status_code == 429
        body = _error_body(resp)
        assert body["ok"] is False
        assert "rate_limit_per_source" in body["error"]
        assert "5/minute" in body["error"]

    def test_rate_limit_per_source_independent(self, client, auth_headers):
        """Different sources have independent rate limits."""
        # Exhaust hermes-memory-agent's 5/min
        for i in range(5):
            client.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": f"hi{i}", "source": "hermes-memory-agent"},
                headers=auth_headers,
            )
        # coding-agent should still work (independent)
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello", "source": "coding-agent"},
            headers=auth_headers,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# _safe_error helper
# ---------------------------------------------------------------------------

class TestSafeError:
    def test_safe_error_returns_ok_false_error(self):
        from notify_api.app import _safe_error
        result = _safe_error("something went wrong")
        assert result == {"ok": False, "error": "something went wrong"}

    def test_safe_error_caps_long_messages(self):
        from notify_api.app import _safe_error
        long_msg = "x" * 200
        result = _safe_error(long_msg)
        assert len(result["error"]) < len(long_msg)
        assert result["error"].endswith("...[truncated]")

    def test_safe_error_redacts_slack_token_in_message(self):
        from notify_api.app import _safe_error
        result = _safe_error("invalid token xoxb-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdef failed")
        # Credential pattern detected → message replaced with "request_error"
        assert result["error"] == "request_error"

    def test_safe_error_redacts_bearer_token_in_message(self):
        from notify_api.app import _safe_error
        result = _safe_error("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 is expired")
        assert result["error"] == "request_error"

    def test_safe_error_redacts_op_ref_in_message(self):
        from notify_api.app import _safe_error
        result = _safe_error("op://vault/item/field not found")
        assert result["error"] == "request_error"

    def test_safe_error_passes_normal_messages(self):
        from notify_api.app import _safe_error
        result = _safe_error("rate_limit_per_source: 5/minute exceeded for hermes-memory-agent")
        assert result == {"ok": False, "error": "rate_limit_per_source: 5/minute exceeded for hermes-memory-agent"}


# ---------------------------------------------------------------------------
# 503 vs 401 — backend-not-configured vs client-auth
# ---------------------------------------------------------------------------

class TestAuthStatusCodes:
    def test_missing_tool_key_returns_503_when_unset(self, app):
        """NOTIFY_TOOL_API_KEY unset → 503 endpoint_not_configured (not 401)."""
        orig = os.environ.get("NOTIFY_TOOL_API_KEY")
        try:
            os.environ.pop("NOTIFY_TOOL_API_KEY", None)
            from fastapi.testclient import TestClient
            c = TestClient(app, raise_server_exceptions=True)
            resp = c.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": "hello"},
                headers={"Authorization": "Bearer any-value"},
            )
            assert resp.status_code == 503
            body = _error_body(resp)
            assert body["ok"] is False
            assert body["error"] == "endpoint_not_configured"
        finally:
            if orig is not None:
                os.environ["NOTIFY_TOOL_API_KEY"] = orig

    def test_wrong_token_returns_401(self, client, invalid_auth_headers):
        """Wrong Bearer token → 401 (not 503)."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers=invalid_auth_headers,
        )
        assert resp.status_code == 401
        body = _error_body(resp)
        assert body["ok"] is False
        assert body["error"] == "invalid_token"

    def test_missing_bearer_prefix_returns_401(self, client):
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers={"Authorization": "Basic abc"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Slack error handling
# ---------------------------------------------------------------------------

class TestSlackErrorHandling:
    def test_slack_not_configured_returns_503_ok_false(self, app):
        """When SLACK_BOT_TOKEN or SLACK_PAULO_USER_ID is unset, returns 503 {ok: false, error}."""
        orig = os.environ.get("SLACK_BOT_TOKEN")
        try:
            os.environ.pop("SLACK_BOT_TOKEN", None)
            from fastapi.testclient import TestClient
            c = TestClient(app, raise_server_exceptions=True)
            resp = c.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": "hello"},
                headers={"Authorization": "Bearer test-tool-key-12345"},
            )
            assert resp.status_code == 503
            body = _error_body(resp)
            assert body["ok"] is False
            assert body["error"] == "endpoint_not_configured"
        finally:
            if orig is not None:
                os.environ["SLACK_BOT_TOKEN"] = orig
            elif "SLACK_BOT_TOKEN" not in os.environ:
                os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-placeholder"

    def test_dry_run_placeholder_token_returns_success(self, client, auth_headers):
        """xoxb-test-placeholder token: dry-run, no real Slack call, returns {ok: true}."""
        resp = client.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["slack_ts"] == "dry_run.12345"

    def test_error_response_never_contains_token(self, app):
        """All error responses must be safe: no Slack token, no Bearer key, no Slack user ID."""
        from fastapi.testclient import TestClient
        c = TestClient(app, raise_server_exceptions=True)

        # Wrong token → 401
        resp = c.post(
            "/tools/notify-paulo",
            json={"title": "test", "message": "hello"},
            headers={"Authorization": "Bearer wrong"},
        )
        body_str = str(resp.json()).lower()
        assert "xoxb" not in body_str
        assert "slack_bot_token" not in body_str
        assert "test-tool-key-12345" not in body_str

        # Missing config → 503
        orig = os.environ.get("SLACK_BOT_TOKEN")
        try:
            os.environ.pop("SLACK_BOT_TOKEN", None)
            resp = c.post(
                "/tools/notify-paulo",
                json={"title": "test", "message": "hello"},
                headers={"Authorization": "Bearer test-tool-key-12345"},
            )
            body_str = str(resp.json()).lower()
            assert "xoxb" not in body_str
            assert "slack_bot_token" not in body_str
        finally:
            if orig:
                os.environ["SLACK_BOT_TOKEN"] = orig


# ---------------------------------------------------------------------------
# Legacy WhatsApp endpoint unaffected
# ---------------------------------------------------------------------------

class TestLegacyWhatsAppUnaffected:
    def test_v1_messages_still_works(self, client):
        """POST /v1/messages should still work with empty X-API-Key (legacy fail-open)."""
        with patch.object(_notify_api_app, "_redis") as mock_redis:
            mock_redis_instance = MagicMock()
            mock_redis.return_value = mock_redis_instance

            resp = client.post(
                "/v1/messages",
                json={
                    "text": "hello",
                    "recipients": ["+1234567890"],
                    "source": "test",
                    "priority": "normal",
                },
                headers={"X-API-Key": ""},
            )
            assert resp.status_code == 200
            assert "message_id" in resp.json()

    def test_v1_messages_requires_key_when_configured(self):
        """When NOTIFY_API_KEY is set, /v1/messages requires the correct key."""
        with patch.dict(os.environ, {"NOTIFY_API_KEY": "my-secret-key"}):
            with patch.object(_notify_api_app, "_redis") as mock_redis:
                mock_redis_instance = MagicMock()
                mock_redis.return_value = mock_redis_instance
                from fastapi.testclient import TestClient
                c = TestClient(_notify_api_app.app, raise_server_exceptions=True)

                # Wrong key → 401
                resp = c.post(
                    "/v1/messages",
                    json={"text": "hello", "recipients": ["+1234567890"]},
                    headers={"X-API-Key": "wrong-key"},
                )
                assert resp.status_code == 401

                # Correct key → 200
                resp = c.post(
                    "/v1/messages",
                    json={"text": "hello", "recipients": ["+1234567890"]},
                    headers={"X-API-Key": "my-secret-key"},
                )
                assert resp.status_code == 200
