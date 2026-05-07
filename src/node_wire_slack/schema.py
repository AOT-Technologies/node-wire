"""
Pydantic v2 input/output models for the Slack connector.

All input models include an `action` discriminator field so that
BaseConnector can build a discriminated union and route to the correct
@sdk_action method automatically — the same pattern used by Google Drive.

Only `channel` and `message` are required for messaging actions.
Authentication is handled via SecretProvider (never hard-coded here).
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared input base
# ---------------------------------------------------------------------------

class _BaseSlackInput(BaseModel):
    """Fields shared by every Slack input model."""

    model_config = ConfigDict(extra="forbid")

    token_secret_key: str = Field(
        default="SLACK_BOT_TOKEN",
        description=(
            "SecretProvider key that holds the Slack Bot Token (xoxb-…). "
            "Override only when running multiple bots."
        ),
    )


# ---------------------------------------------------------------------------
# Action: post_message
# ---------------------------------------------------------------------------

class SlackPostMessageInput(_BaseSlackInput):
    """Send a message to a Slack channel."""

    action: Literal["post_message"] = "post_message"
    channel: str = Field(..., description="Target Channel ID (C…), Name (#general), or User ID (U…).")
    message: str = Field(..., description="Plain-text fallback message (markdown supported).")
    blocks: Optional[Union[str, List[Any]]] = Field(
        default=None,
        description="Block Kit payload as a JSON string or a pre-parsed list.",
    )


# ---------------------------------------------------------------------------
# Action: send_direct_message
# ---------------------------------------------------------------------------

class SlackSendDirectMessageInput(_BaseSlackInput):
    """Send a direct message to a Slack user."""

    action: Literal["send_direct_message"] = "send_direct_message"
    channel: str = Field(..., description="Target User ID (U…), Channel ID (C…), or Name (#general).")
    message: str = Field(..., description="Plain-text fallback message (markdown supported).")
    blocks: Optional[Union[str, List[Any]]] = Field(
        default=None,
        description="Block Kit payload as a JSON string or a pre-parsed list.",
    )


# ---------------------------------------------------------------------------
# Action: upload_file
# ---------------------------------------------------------------------------

class SlackUploadFileInput(_BaseSlackInput):
    """Upload a file to a Slack channel or DM via the external-upload API."""

    action: Literal["upload_file"] = "upload_file"
    channel: str = Field(..., description="Target Channel ID, Name, or User ID to share the file with.")
    filename: str = Field(default="", description="Display name for the uploaded file.")
    initial_comment: str = Field(default="", description="Message posted alongside the file.")
    filepath: str = Field(
        default="",
        description=(
            "Absolute path to a file under the sandboxed attachments directory "
            "(NW_SLACK_ATTACHMENTS_DIR). Mutually exclusive with content_base64."
        ),
    )
    content_base64: str = Field(
        default="",
        description="Base64-encoded file content. Mutually exclusive with filepath.",
    )


# ---------------------------------------------------------------------------
# Discriminated union — used by BaseConnector internally
# ---------------------------------------------------------------------------

_SlackOperationUnion = Annotated[
    Union[
        SlackPostMessageInput,
        SlackSendDirectMessageInput,
        SlackUploadFileInput,
    ],
    Field(discriminator="action"),
]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class SlackOutput(BaseModel):
    """Unified output envelope for all Slack actions."""

    ok: bool = Field(..., description="True when Slack acknowledged the request.")
    ts: Optional[str] = Field(default=None, description="Message timestamp (chat actions).")
    file_id: Optional[str] = Field(default=None, description="File ID (upload action).")
    channel: Optional[str] = Field(default=None, description="Channel the message was sent to.")
    description: str = Field(default="", description="Human-readable summary of the outcome.")
    raw: Dict[str, Any] = Field(default_factory=dict, description="Full Slack API response.")
