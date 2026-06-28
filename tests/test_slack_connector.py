#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Unit tests for the Node-Wire Slack connector.

All tests are fully offline — httpx calls inside logic.py are patched with
unittest.mock so no real Slack API is contacted.

Coverage
--------
- Connector instantiation and connector_id
- post_message happy path
- post_message with Block Kit (string + list)
- post_message with invalid blocks JSON
- post_message: SlackAuthError maps to ErrorCategory.AUTH
- post_message: SlackRateLimitError maps to ErrorCategory.RETRYABLE
- send_direct_message happy path
- upload_file base64 happy path (all 3 upload steps mocked)
- upload_file filepath happy path
- upload_file: missing content source raises SlackUploadError
- upload_file: invalid base64 raises SlackUploadError
- upload_file: file exceeds size limit
- Token is NEVER present in log output (security boundary)
"""

from __future__ import annotations

import base64
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from node_wire_runtime import BaseConnector, ErrorCategory
from node_wire_runtime.secrets import SecretProvider

from node_wire_slack.exceptions import (
    SlackAuthError,
    SlackMessageError,
    SlackPermissionError,
    SlackRateLimitError,
    SlackUploadError,
)
from node_wire_slack.logic import (
    SlackConnector,
    _complete_upload,
    _get_upload_url,
    _resolve_blocks,
    _upload_bytes,
)
import node_wire_slack.registration  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_TOKEN = "xoxb-test-token-0000"
_CHANNEL = "C0TEST123"
_USER_ID = "U0TEST456"


class FakeSecretProvider(SecretProvider):
    """Returns the fake token for any key."""

    def get_secret(self, key: str) -> str:
        return _FAKE_TOKEN


def _make_connector() -> SlackConnector:
    return SlackConnector(secret_provider=FakeSecretProvider())


def _slack_ok_response(**extra: Any) -> dict[str, Any]:
    return {"ok": True, "ts": "1234567890.123456", "channel": _CHANNEL, **extra}


# ---------------------------------------------------------------------------
# 1. Instantiation
# ---------------------------------------------------------------------------


def test_slack_connector_instantiation() -> None:
    connector = _make_connector()
    assert connector.connector_id == "slack"
    assert isinstance(connector, BaseConnector)


def test_slack_connector_has_three_actions() -> None:
    metas = SlackConnector.sdk_action_metas()
    assert set(metas.keys()) == {"post_message", "send_direct_message", "upload_file"}


# ---------------------------------------------------------------------------
# 2. post_message — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_success() -> None:
    connector = _make_connector()
    response_data = _slack_ok_response()

    with patch("node_wire_slack.logic._post_json", new=AsyncMock(return_value=response_data)):
        result = await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hello World"}
        )

    assert result.success is True
    assert result.data["ok"] is True
    assert result.data["channel"] == _CHANNEL


# ---------------------------------------------------------------------------
# 3. post_message with Block Kit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_with_blocks_list() -> None:
    """Blocks provided as a list are forwarded directly."""
    connector = _make_connector()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
    captured: dict[str, Any] = {}

    async def fake_post_json(url: str, token: str, body: dict) -> dict:
        captured.update(body)
        return _slack_ok_response()

    with patch("node_wire_slack.logic._post_json", new=fake_post_json):
        result = await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hi", "blocks": blocks}
        )

    assert result.success is True
    assert captured.get("blocks") == blocks


@pytest.mark.asyncio
async def test_post_message_with_blocks_json_string() -> None:
    """Blocks provided as a JSON string are parsed before being sent."""
    connector = _make_connector()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}]
    import json

    blocks_str = json.dumps(blocks)
    captured: dict[str, Any] = {}

    async def fake_post_json(url: str, token: str, body: dict) -> dict:
        captured.update(body)
        return _slack_ok_response()

    with patch("node_wire_slack.logic._post_json", new=fake_post_json):
        await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hi", "blocks": blocks_str}
        )

    assert captured.get("blocks") == blocks


@pytest.mark.asyncio
async def test_post_message_invalid_blocks_json_returns_error() -> None:
    """Invalid blocks JSON must map to a BUSINESS error response, not an unhandled crash."""
    connector = _make_connector()

    result = await connector.run(
        {"action": "post_message", "channel": _CHANNEL, "message": "Hi", "blocks": "{not-json"}
    )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS


# ---------------------------------------------------------------------------
# 4. post_message — auth error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_auth_error_maps_to_auth_category() -> None:
    connector = _make_connector()

    with patch(
        "node_wire_slack.logic._post_json",
        new=AsyncMock(side_effect=SlackAuthError("token_revoked")),
    ):
        result = await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hi"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.AUTH
    assert result.error_code == "SLACK_AUTH_ERROR"


# ---------------------------------------------------------------------------
# 5. post_message — permission error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_permission_error_maps_to_auth_category() -> None:
    connector = _make_connector()

    with patch(
        "node_wire_slack.logic._post_json",
        new=AsyncMock(side_effect=SlackPermissionError("missing_scope")),
    ):
        result = await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hi"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.AUTH
    assert result.error_code == "SLACK_PERMISSION_ERROR"


# ---------------------------------------------------------------------------
# 6. post_message — rate limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_message_rate_limit_maps_to_retryable() -> None:
    connector = _make_connector()

    with patch(
        "node_wire_slack.logic._post_json",
        new=AsyncMock(side_effect=SlackRateLimitError("ratelimited")),
    ):
        result = await connector.run(
            {"action": "post_message", "channel": _CHANNEL, "message": "Hi"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.RETRYABLE
    assert result.error_code == "SLACK_RATE_LIMIT"


# ---------------------------------------------------------------------------
# 7. send_direct_message — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_direct_message_success() -> None:
    connector = _make_connector()
    response_data = {**_slack_ok_response(), "channel": _USER_ID}

    with patch("node_wire_slack.logic._post_json", new=AsyncMock(return_value=response_data)):
        result = await connector.run(
            {"action": "send_direct_message", "channel": _USER_ID, "message": "Hey!"}
        )

    assert result.success is True
    assert result.data["ok"] is True


@pytest.mark.asyncio
async def test_send_direct_message_with_blocks() -> None:
    connector = _make_connector()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "dm blocks"}}]
    captured: dict[str, Any] = {}

    async def fake_post_json(url: str, token: str, body: dict) -> dict:
        captured.update(body)
        return {**_slack_ok_response(), "channel": _USER_ID}

    with patch("node_wire_slack.logic._post_json", new=fake_post_json):
        result = await connector.run(
            {
                "action": "send_direct_message",
                "channel": _USER_ID,
                "message": "Hi",
                "blocks": blocks,
            }
        )

    assert result.success is True
    assert captured.get("blocks") == blocks


# ---------------------------------------------------------------------------
# 8. upload_file — base64 happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_base64_success() -> None:
    connector = _make_connector()
    content = b"Hello, file!"
    b64 = base64.b64encode(content).decode()
    file_id = "F0TESTFILE"

    complete_response = {"ok": True, "files": [{"id": file_id}]}

    with (
        patch(
            "node_wire_slack.logic._get_upload_url",
            new=AsyncMock(return_value=("https://upload.slack.com/test", file_id)),
        ),
        patch("node_wire_slack.logic._upload_bytes", new=AsyncMock(return_value=None)),
        patch(
            "node_wire_slack.logic._complete_upload", new=AsyncMock(return_value=complete_response)
        ),
    ):
        result = await connector.run(
            {
                "action": "upload_file",
                "channel": _CHANNEL,
                "filename": "test.txt",
                "content_base64": b64,
            }
        )

    assert result.success is True
    assert result.data["file_id"] == file_id


@pytest.mark.asyncio
async def test_upload_file_missing_filepath_in_sandbox_returns_business_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    monkeypatch.setattr("node_wire_slack.logic._ATTACHMENTS_DIR", str(attachments_dir))

    connector = _make_connector()
    missing = attachments_dir / "missing.txt"

    result = await connector.run(
        {
            "action": "upload_file",
            "channel": _CHANNEL,
            "filepath": str(missing),
        }
    )

    assert result.success is False
    assert result.error_code == "SLACK_UPLOAD_ERROR"
    assert "No such file" in result.message


# ---------------------------------------------------------------------------
# 9. upload_file — missing content source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_missing_content_returns_business_error() -> None:
    connector = _make_connector()

    result = await connector.run(
        {"action": "upload_file", "channel": _CHANNEL, "filename": "empty.txt"}
    )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "SLACK_UPLOAD_ERROR"


# ---------------------------------------------------------------------------
# 10. upload_file — invalid base64
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_invalid_base64_returns_business_error() -> None:
    connector = _make_connector()

    result = await connector.run(
        {
            "action": "upload_file",
            "channel": _CHANNEL,
            "content_base64": "!!!not-valid-base64!!!",
        }
    )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "SLACK_UPLOAD_ERROR"


# ---------------------------------------------------------------------------
# 11. upload_file — file too large
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_too_large_returns_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector = _make_connector()
    monkeypatch.setenv("NW_SLACK_UPLOAD_LIMIT_MB", "1")

    # 2 MB of content
    content = b"x" * (2 * 1024 * 1024)
    b64 = base64.b64encode(content).decode()

    result = await connector.run(
        {"action": "upload_file", "channel": _CHANNEL, "content_base64": b64}
    )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "SLACK_UPLOAD_ERROR"


# ---------------------------------------------------------------------------
# 12. Security: token never in logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """The Slack bot token must NEVER appear in any log record."""
    connector = _make_connector()

    with caplog.at_level(logging.DEBUG, logger="connectors.slack"):
        with patch(
            "node_wire_slack.logic._post_json",
            new=AsyncMock(return_value=_slack_ok_response()),
        ):
            await connector.run(
                {"action": "post_message", "channel": _CHANNEL, "message": "secure"}
            )

    for record in caplog.records:
        assert _FAKE_TOKEN not in record.getMessage(), (
            f"Token leaked in log record: {record.getMessage()!r}"
        )
        assert _FAKE_TOKEN not in str(record.__dict__), (
            f"Token leaked in log record attrs: {record.__dict__!r}"
        )


# ---------------------------------------------------------------------------
# 13. _resolve_blocks helper
# ---------------------------------------------------------------------------


def test_resolve_blocks_none_returns_none() -> None:
    assert _resolve_blocks(None) is None


def test_resolve_blocks_list_passthrough() -> None:
    blocks = [{"type": "section"}]
    assert _resolve_blocks(blocks) == blocks


def test_resolve_blocks_valid_json_string() -> None:
    import json

    blocks = [{"type": "section"}]
    assert _resolve_blocks(json.dumps(blocks)) == blocks


def test_resolve_blocks_invalid_json_raises() -> None:
    with pytest.raises(SlackMessageError, match="Invalid blocks JSON"):
        _resolve_blocks("{bad json")


def test_resolve_blocks_non_array_json_raises() -> None:
    import json

    with pytest.raises(SlackMessageError, match="must be a JSON array"):
        _resolve_blocks(json.dumps({"type": "section"}))


def test_resolve_blocks_empty_string_returns_none() -> None:
    assert _resolve_blocks("   ") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", ["", "#general", "U0TEST456"])
async def test_complete_upload_omits_invalid_channel_id(channel_id: str) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {"ok": True, "files": [{"id": "F0TESTFILE"}]}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            data: dict[str, Any] | None = None,
        ) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["data"] = data or {}
            return FakeResponse()

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=FakeAsyncClient):
        data = await _complete_upload(
            _FAKE_TOKEN,
            "F0TESTFILE",
            "test.txt",
            channel_id=channel_id,
            initial_comment="hello",
        )

    assert data["ok"] is True
    assert "channel_id" not in captured["data"]
    assert captured["data"]["initial_comment"] == "hello"


@pytest.mark.asyncio
async def test_upload_file_invalid_resolved_channel_returns_business_error() -> None:
    connector = _make_connector()
    b64 = base64.b64encode(b"Hello, file!").decode()

    with (
        patch("node_wire_slack.logic._resolve_channel_id", new=AsyncMock(return_value="#general")),
        patch("node_wire_slack.logic._get_upload_url", new=AsyncMock()) as get_upload_url,
    ):
        result = await connector.run(
            {
                "action": "upload_file",
                "channel": "#general",
                "filename": "test.txt",
                "content_base64": b64,
            }
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "SLACK_UPLOAD_ERROR"
    assert "Could not resolve '#general' to a valid Slack channel ID" in result.message
    get_upload_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_channel_id_network_error_falls_back_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q-4: a network error resolving a User ID falls back to the target and logs structured context."""
    import httpx

    from node_wire_slack.logic import _resolve_channel_id

    # The repo .env sets NW_SLACK_SKIP_RESOLVE=true; clear it so the resolution
    # (and its error path) actually runs when other tests have loaded dotenv.
    monkeypatch.delenv("NW_SLACK_SKIP_RESOLVE", raising=False)

    class _RaisingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> "_RaisingClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError("connection refused")

    # Attach a handler directly to the connector logger so capture is independent
    # of global logging state mutated by other test modules in the full suite.
    slack_logger = logging.getLogger("connectors.slack")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    prev_level = slack_logger.level
    slack_logger.addHandler(handler)
    slack_logger.setLevel(logging.WARNING)
    try:
        with patch("node_wire_slack.logic.httpx.AsyncClient", new=_RaisingClient):
            resolved = await _resolve_channel_id("xoxb-fake-token", "U12345678")
    finally:
        slack_logger.removeHandler(handler)
        slack_logger.setLevel(prev_level)

    assert resolved == "U12345678"  # fell back to original target
    warnings = [r for r in records if "Network error resolving" in r.getMessage()]
    assert warnings, "expected a structured network-error warning"
    assert getattr(warnings[0], "error_type", None) == "ConnectError"


@pytest.mark.asyncio
async def test_upload_file_filepath_happy_path(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    upload_path = attachments_dir / "note.txt"
    upload_path.write_bytes(b"file data")
    monkeypatch.setattr("node_wire_slack.logic._ATTACHMENTS_DIR", str(attachments_dir))

    connector = _make_connector()
    file_id = "F0TESTFILE"
    complete_response = {"ok": True, "files": [{"id": file_id}]}

    with (
        patch(
            "node_wire_slack.logic._get_upload_url",
            new=AsyncMock(return_value=("https://upload.slack.com/test", file_id)),
        ),
        patch("node_wire_slack.logic._upload_bytes", new=AsyncMock(return_value=None)),
        patch(
            "node_wire_slack.logic._complete_upload", new=AsyncMock(return_value=complete_response)
        ),
    ):
        result = await connector.run(
            {
                "action": "upload_file",
                "channel": _CHANNEL,
                "filepath": str(upload_path),
            }
        )

    assert result.success is True
    assert result.data["file_id"] == file_id


@pytest.mark.asyncio
async def test_get_upload_url_missing_fields_raises() -> None:
    class FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"ok": True, "file_id": "F1"}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=FakeAsyncClient):
        with pytest.raises(SlackUploadError, match="upload_url or file_id"):
            await _get_upload_url(_FAKE_TOKEN, "test.txt", 8)


@pytest.mark.asyncio
async def test_upload_bytes_non_200_raises_slack_upload_error() -> None:
    class FakeResponse:
        status_code = 500

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=FakeAsyncClient):
        with pytest.raises(SlackUploadError, match="HTTP 500"):
            await _upload_bytes("https://upload.slack.com/test", b"data")


def test_default_timeout_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q-7: _DEFAULT_TIMEOUT is configurable via NW_SLACK_TIMEOUT / NW_TIMEOUT."""
    import importlib

    import node_wire_slack.logic as slack_logic

    monkeypatch.setenv("NW_SLACK_TIMEOUT", "12.5")
    try:
        reloaded = importlib.reload(slack_logic)
        assert reloaded._DEFAULT_TIMEOUT == 12.5
    finally:
        # Restore the module to its default-env state for the rest of the suite.
        monkeypatch.delenv("NW_SLACK_TIMEOUT", raising=False)
        importlib.reload(slack_logic)


# ---------------------------------------------------------------------------
# 14. _post_json — Slack API error translation via ok:false payloads
# ---------------------------------------------------------------------------


def _fake_slack_client(response_json: dict[str, Any], status_code: int = 200):
    """Build a fake httpx.AsyncClient that returns a fixed JSON body."""

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = status_code

        def json(self) -> dict[str, Any]:
            return response_json

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> FakeResponse:
            return FakeResponse()

    return FakeAsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code,expected_exc",
    [
        ("invalid_auth", SlackAuthError),
        ("missing_scope", SlackPermissionError),
        ("ratelimited", SlackRateLimitError),
        ("channel_not_found", SlackMessageError),
    ],
)
async def test_post_json_ok_false_raises_typed_exception(
    error_code: str, expected_exc: type[Exception]
) -> None:
    from node_wire_slack.logic import _post_json

    payload = {
        "ok": False,
        "error": error_code,
        "response_metadata": {"messages": ["detail message"]},
    }

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=_fake_slack_client(payload)):
        with pytest.raises(expected_exc):
            await _post_json("https://slack.com/api/chat.postMessage", _FAKE_TOKEN, {"text": "hi"})


@pytest.mark.asyncio
async def test_post_json_http_429_raises_rate_limit() -> None:
    from node_wire_slack.logic import _post_json

    payload = {"ok": False, "error": "some_other_error"}

    with patch(
        "node_wire_slack.logic.httpx.AsyncClient",
        new=_fake_slack_client(payload, status_code=429),
    ):
        with pytest.raises(SlackRateLimitError):
            await _post_json("https://slack.com/api/chat.postMessage", _FAKE_TOKEN, {"text": "hi"})


@pytest.mark.asyncio
async def test_complete_upload_ok_false_raises() -> None:
    payload = {"ok": False, "error": "invalid_auth"}

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=_fake_slack_client(payload)):
        with pytest.raises(SlackAuthError):
            await _complete_upload(_FAKE_TOKEN, "F0TEST", "title.txt", channel_id=_CHANNEL)


# ---------------------------------------------------------------------------
# 15. _resolve_channel_id — user DM success and ok:false fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_channel_id_user_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_slack.logic import _resolve_channel_id

    monkeypatch.delenv("NW_SLACK_SKIP_RESOLVE", raising=False)

    dm_channel = "D0DMCHANNEL"

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> object:
            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": True, "channel": {"id": dm_channel}}

            return FakeResponse()

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=FakeAsyncClient):
        resolved = await _resolve_channel_id(_FAKE_TOKEN, "U12345678")

    assert resolved == dm_channel


@pytest.mark.asyncio
async def test_resolve_channel_id_user_ok_false_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_wire_slack.logic import _resolve_channel_id

    monkeypatch.delenv("NW_SLACK_SKIP_RESOLVE", raising=False)
    user_id = "U87654321"

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> object:
            class FakeResponse:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"ok": False, "error": "user_not_found"}

            return FakeResponse()

    with patch("node_wire_slack.logic.httpx.AsyncClient", new=FakeAsyncClient):
        resolved = await _resolve_channel_id(_FAKE_TOKEN, user_id)

    assert resolved == user_id


@pytest.mark.asyncio
async def test_resolve_channel_id_empty_target_returns_empty() -> None:
    from node_wire_slack.logic import _resolve_channel_id

    assert await _resolve_channel_id(_FAKE_TOKEN, "   ") == ""


# ---------------------------------------------------------------------------
# 16. Upload path and limit helpers
# ---------------------------------------------------------------------------


def test_resolve_upload_path_relative_raises(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_slack.logic import _resolve_upload_path

    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    monkeypatch.setattr("node_wire_slack.logic._ATTACHMENTS_DIR", str(attachments_dir))

    with pytest.raises(SlackUploadError, match="must be an absolute path"):
        _resolve_upload_path("relative/note.txt")


def test_resolve_upload_path_escape_raises(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_slack.logic import _resolve_upload_path

    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    monkeypatch.setattr("node_wire_slack.logic._ATTACHMENTS_DIR", str(attachments_dir))

    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    with pytest.raises(SlackUploadError, match="must be under"):
        _resolve_upload_path(str(outside))


def test_get_upload_limit_bytes_invalid_env_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_wire_slack.logic import _get_upload_limit_bytes

    monkeypatch.setenv("NW_SLACK_UPLOAD_LIMIT_MB", "not-a-number")
    assert _get_upload_limit_bytes() == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_resolve_channel_id_z_prefix_passthrough() -> None:
    from node_wire_slack.logic import _resolve_channel_id

    channel = "Z0123456789"
    assert await _resolve_channel_id(_FAKE_TOKEN, channel) == channel
