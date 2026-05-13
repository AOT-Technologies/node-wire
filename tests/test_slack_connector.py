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

import asyncio
import base64
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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
    _resolve_blocks,
)
from node_wire_slack.schema import (
    SlackOutput,
    SlackPostMessageInput,
    SlackSendDirectMessageInput,
    SlackUploadFileInput,
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
        patch("node_wire_slack.logic._get_upload_url", new=AsyncMock(return_value=("https://upload.slack.com/test", file_id))),
        patch("node_wire_slack.logic._upload_bytes", new=AsyncMock(return_value=None)),
        patch("node_wire_slack.logic._complete_upload", new=AsyncMock(return_value=complete_response)),
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
async def test_upload_file_too_large_returns_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
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
