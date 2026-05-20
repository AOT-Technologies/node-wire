"""
Slack connector for Node-Wire.

Structure mirrors node_wire_smtp/logic.py:
  - Private async HTTP helpers at module level (no separate helper file).
  - SlackConnector(BaseConnector) with one @sdk_action per operation.

The Slack Bot Token is NEVER logged or included in exceptions.
All credentials are resolved at call-time via SecretProvider.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
from typing import Any

import httpx

from node_wire_runtime import BaseConnector, sdk_action

from .exceptions import (
    SlackAuthError,
    SlackMessageError,
    SlackPermissionError,
    SlackRateLimitError,
    SlackUploadError,
)
from .schema import (
    SlackOutput,
    SlackPostMessageInput,
    SlackSendDirectMessageInput,
    SlackUploadFileInput,
)

logger = logging.getLogger("connectors.slack")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAT_POST_URL = "https://slack.com/api/chat.postMessage"
_GET_UPLOAD_URL = "https://slack.com/api/files.getUploadURLExternal"
_COMPLETE_UPLOAD_URL = "https://slack.com/api/files.completeUploadExternal"

_DEFAULT_TIMEOUT = 30.0
_HARD_UPLOAD_LIMIT_MB = 100
_DEFAULT_UPLOAD_LIMIT_MB = 50
_CHANNEL_ID_RE = re.compile(r"^[CGDZ][A-Z0-9]{8,}$")


def _get_api_url(path: str) -> str:
    """Helper to construct Slack API URLs, allowing base URL override via NW_SLACK_API_BASE_URL."""
    base = os.environ.get("NW_SLACK_API_BASE_URL", "https://slack.com/api").rstrip("/")
    return f"{base}/{path.lstrip('/')}"


_CHAT_POST_URL = _get_api_url("chat.postMessage")
_GET_UPLOAD_URL = _get_api_url("files.getUploadURLExternal")
_COMPLETE_UPLOAD_URL = _get_api_url("files.completeUploadExternal")

# Sandboxed directory for filesystem-based uploads.
_ATTACHMENTS_DIR = os.environ.get("NW_SLACK_ATTACHMENTS_DIR", "/slack_attachments")

# Slack error strings that map to specific domain exceptions.
_AUTH_ERRORS = frozenset({"invalid_auth", "token_revoked", "account_inactive", "not_authed"})
_SCOPE_ERRORS = frozenset({"missing_scope", "invalid_scopes"})
_RATE_ERRORS = frozenset({"ratelimited"})


# ---------------------------------------------------------------------------
# Private HTTP helpers  (module-level, not a separate file)
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_valid_channel_id(value: str) -> bool:
    """Return True when *value* matches Slack's channel_id format."""
    return bool(_CHANNEL_ID_RE.fullmatch(value.strip()))


def _raise_for_slack_error(response_json: dict[str, Any], http_status: int) -> None:
    """Translate a Slack `ok: false` payload into a typed domain exception."""
    slack_error = response_json.get("error", "unknown")
    messages = response_json.get("response_metadata", {}).get("messages", [])
    detail = ". ".join(messages) if messages else str(slack_error)

    if slack_error in _AUTH_ERRORS:
        raise SlackAuthError("Slack authentication failed or token was revoked.")
    if slack_error in _SCOPE_ERRORS:
        raise SlackPermissionError(f"Slack permission error: {detail}")
    if slack_error in _RATE_ERRORS or http_status == 429:
        raise SlackRateLimitError(f"Slack rate limit: {detail}")
    raise SlackMessageError(f"Slack API error '{slack_error}': {detail}")


async def _post_json(
    url: str,
    token: str,
    body: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """POST JSON to the Slack API. Raises a typed exception on failure."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json=body,
        )
    data = response.json()
    if not data.get("ok"):
        _raise_for_slack_error(data, response.status_code)
    return data


async def _get_upload_url(
    token: str,
    filename: str,
    length: int,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[str, str]:
    """Step 1 of the external upload flow. Returns (upload_url, file_id)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            _GET_UPLOAD_URL,
            headers=_auth_headers(token),
            data={"filename": filename, "length": str(length)},
        )
    data = response.json()
    if not data.get("ok"):
        _raise_for_slack_error(data, response.status_code)
    upload_url = data.get("upload_url", "")
    file_id = data.get("file_id", "")
    if not upload_url or not file_id:
        raise SlackUploadError("Slack did not return upload_url or file_id.")
    return upload_url, file_id


async def _upload_bytes(
    upload_url: str,
    content: bytes,
    timeout: float = _DEFAULT_TIMEOUT,
) -> None:
    """Step 2: PUT raw bytes to the pre-signed URL Slack returned."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            upload_url,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
    if response.status_code != 200:
        raise SlackUploadError(f"Upload to pre-signed URL failed with HTTP {response.status_code}.")


async def _complete_upload(
    token: str,
    file_id: str,
    title: str,
    channel_id: str = "",
    initial_comment: str = "",
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Step 3: Finalise the upload and optionally share to a channel."""
    data: dict[str, Any] = {
        "files": json.dumps([{"id": file_id, "title": title}]),
    }
    if _is_valid_channel_id(channel_id):
        data["channel_id"] = channel_id
    if initial_comment:
        data["initial_comment"] = initial_comment

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            _COMPLETE_UPLOAD_URL,
            headers=_auth_headers(token),
            data=data,
        )
    resp_data = response.json()
    if not resp_data.get("ok"):
        _raise_for_slack_error(resp_data, response.status_code)
    return resp_data


def _resolve_blocks(blocks: Any) -> list[Any] | None:
    """Parse Block Kit payload from a JSON string or pass through a list.
    Raises SlackMessageError on invalid JSON."""
    if blocks is None:
        return None
    if isinstance(blocks, list):
        return blocks
    if isinstance(blocks, str) and blocks.strip():
        try:
            parsed = json.loads(blocks)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SlackMessageError(f"Invalid blocks JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise SlackMessageError("blocks must be a JSON array.")
        return parsed
    return None


def _get_upload_limit_bytes() -> int:
    raw = os.environ.get("NW_SLACK_UPLOAD_LIMIT_MB", "")
    try:
        mb = int(raw.strip()) if raw.strip() else _DEFAULT_UPLOAD_LIMIT_MB
    except ValueError:
        mb = _DEFAULT_UPLOAD_LIMIT_MB
    mb = max(1, min(mb, _HARD_UPLOAD_LIMIT_MB))
    return mb * 1024 * 1024


def _resolve_upload_path(filepath: str) -> str:
    """Validate that *filepath* is under the sandboxed attachments directory."""
    allowed = os.path.realpath(_ATTACHMENTS_DIR)
    if not os.path.isabs(filepath):
        raise SlackUploadError(
            f"filepath must be an absolute path under '{allowed}'. Got: {filepath!r}"
        )
    candidate = os.path.realpath(filepath)
    if candidate != allowed and not candidate.startswith(allowed + os.sep):
        raise SlackUploadError(f"filepath must be under '{allowed}'. Got: {filepath!r}")
    return candidate


async def _resolve_channel_id(token: str, target: str) -> str:
    """
    Resolve a target (Channel name, Channel ID, or User ID) to a Slack Channel ID.
    - If NW_SLACK_SKIP_RESOLVE=true, returns target as-is (useful for mocks/restricted envs).
    - If it already looks like a Channel ID (C, G, D), return it.
    - If it starts with U or W, it's a User ID; call conversations.open to get the DM channel.
    - Otherwise, return as-is (names like #general are handled natively by chat.postMessage).
    """
    target = target.strip()
    if not target:
        return target

    if os.environ.get("NW_SLACK_SKIP_RESOLVE", "").lower() == "true":
        logger.debug(f"Skipping channel resolution for {target} (NW_SLACK_SKIP_RESOLVE=true)")
        return target

    prefix = target[0].upper()

    # Already a channel/group/dm ID
    if prefix in ("C", "G", "D", "Z"):
        return target

    # User ID -> Resolve to DM channel
    if prefix in ("U", "W"):
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                response = await client.post(
                    _get_api_url("conversations.open"),
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json={"users": target},
                )
                data = response.json()
                if data.get("ok"):
                    resolved_id = data["channel"]["id"]
                    logger.debug(f"Resolved User ID {target} to DM channel {resolved_id}")
                    return resolved_id

                # If Slack returns ok: false, fallback to the original ID
                logger.warning(
                    f"Failed to resolve User ID {target} to DM channel: {data.get('error')}"
                )
                return target
        except Exception as exc:
            # Catch network errors (ConnectError, etc.) and fallback to original ID
            logger.warning(f"Network error resolving User ID {target} to DM channel: {exc}")
            return target

    return target


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class SlackConnector(BaseConnector):
    """
    Slack connector: post messages, send DMs, and upload files to Slack channels.

    Authentication uses a Slack Bot Token (xoxb-…) fetched at call-time via
    SecretProvider. The token is never stored on the instance or emitted in logs.

    Actions
    -------
    post_message         — chat.postMessage to a channel
    send_direct_message  — chat.postMessage to a user DM (same API, by user ID)
    upload_file          — 3-step external upload (getUploadURLExternal flow)
    """

    connector_id = "slack"
    output_model = SlackOutput

    # ------------------------------------------------------------------
    # post_message
    # ------------------------------------------------------------------

    @sdk_action("post_message")
    async def post_message(self, params: SlackPostMessageInput, *, trace_id: str) -> SlackOutput:
        logger.info(
            "Sending Slack channel message",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "post_message",
                "channel": params.channel,
            },
        )
        token = self.secret_provider.get_secret(params.token_secret_key)
        channel_id = await _resolve_channel_id(token, params.channel)

        body: dict[str, Any] = {"channel": channel_id, "text": params.message}
        parsed_blocks = _resolve_blocks(params.blocks)
        if parsed_blocks is not None:
            body["blocks"] = parsed_blocks

        data = await _post_json(_CHAT_POST_URL, token, body)

        logger.info(
            "Slack channel message sent",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "post_message",
                "channel": channel_id,
                "ts": data.get("ts"),
            },
        )
        return SlackOutput(
            ok=True,
            ts=data.get("ts"),
            channel=data.get("channel") or channel_id,
            description=f"Message sent to {params.channel}.",
            raw=data,
        )

    # ------------------------------------------------------------------
    # send_direct_message
    # ------------------------------------------------------------------

    @sdk_action("send_direct_message")
    async def send_direct_message(
        self, params: SlackSendDirectMessageInput, *, trace_id: str
    ) -> SlackOutput:
        logger.info(
            "Sending Slack direct message",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_direct_message",
                "channel": params.channel,
            },
        )
        token = self.secret_provider.get_secret(params.token_secret_key)
        channel_id = await _resolve_channel_id(token, params.channel)

        body: dict[str, Any] = {"channel": channel_id, "text": params.message}
        parsed_blocks = _resolve_blocks(params.blocks)
        if parsed_blocks is not None:
            body["blocks"] = parsed_blocks

        data = await _post_json(_CHAT_POST_URL, token, body)

        logger.info(
            "Slack direct message sent",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_direct_message",
                "channel": channel_id,
                "ts": data.get("ts"),
            },
        )
        return SlackOutput(
            ok=True,
            ts=data.get("ts"),
            channel=data.get("channel") or channel_id,
            description=f"Direct message sent to user {params.channel}.",
            raw=data,
        )

    # ------------------------------------------------------------------
    # upload_file
    # ------------------------------------------------------------------

    @sdk_action("upload_file")
    async def upload_file(self, params: SlackUploadFileInput, *, trace_id: str) -> SlackOutput:
        logger.info(
            "Starting Slack file upload",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "upload_file",
                "channel": params.channel,
            },
        )
        token = self.secret_provider.get_secret(params.token_secret_key)
        channel_id = await _resolve_channel_id(token, params.channel)
        if params.channel and not _is_valid_channel_id(channel_id):
            raise SlackUploadError(
                f"Could not resolve {params.channel!r} to a valid Slack channel ID. "
                "Provide a channel ID (for example C01AB2CD3EF) instead of a channel name."
            )
        limit_bytes = _get_upload_limit_bytes()

        # --- Resolve content bytes ---
        if params.filepath:
            safe_path = _resolve_upload_path(params.filepath)
            if not os.path.isfile(safe_path):
                raise SlackUploadError(f"No such file in upload directory: {params.filepath!r}")
            size = os.path.getsize(safe_path)
            effective_filename = params.filename or os.path.basename(safe_path)
            if size > limit_bytes:
                raise SlackUploadError(
                    f"File '{effective_filename}' is {size / 1024 / 1024:.2f} MB, "
                    f"exceeds limit of {limit_bytes / 1024 / 1024:.0f} MB."
                )
            with open(safe_path, "rb") as fh:
                content_bytes = fh.read()

        elif params.content_base64:
            effective_filename = params.filename or "upload.bin"
            try:
                content_bytes = base64.b64decode(params.content_base64, validate=True)
            except binascii.Error as exc:
                raise SlackUploadError(f"Invalid base64 content: {exc}") from exc
            if len(content_bytes) > limit_bytes:
                raise SlackUploadError(
                    f"Decoded content is {len(content_bytes) / 1024 / 1024:.2f} MB, "
                    f"exceeds limit of {limit_bytes / 1024 / 1024:.0f} MB."
                )

        else:
            raise SlackUploadError("Either 'filepath' or 'content_base64' must be provided.")

        # --- 3-step external upload ---
        logger.info(
            "Requesting upload URL from Slack",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "upload_file",
                "nw_filename": effective_filename,
                "size_bytes": len(content_bytes),
            },
        )
        upload_url, file_id = await _get_upload_url(token, effective_filename, len(content_bytes))

        await _upload_bytes(upload_url, content_bytes)

        data = await _complete_upload(
            token,
            file_id,
            title=effective_filename,
            channel_id=channel_id,
            initial_comment=params.initial_comment,
        )

        logger.info(
            "Slack file upload completed",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "upload_file",
                "channel": channel_id,
                "file_id": file_id,
            },
        )
        return SlackOutput(
            ok=True,
            file_id=file_id,
            channel=params.channel,
            description=f"File '{effective_filename}' uploaded to {params.channel}.",
            raw=data,
        )
