# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Slack Connector — Technical Documentation

> **Platform:** Node Wire
> **Connector ID:** `slack`
> **REST:** One route per operation, e.g. `POST /connectors/slack/post_message`.
> **Discriminator:** `action` field (discriminated-union payload)
> **Source:** `src/node_wire_slack/`

---

## 1. Operations Overview

The Slack connector provides high-level actions for messaging and file management. It follows the standard Node-Wire 4-file structure, ensuring consistent authentication, error handling, and schema validation.

### Architecture
- **Schema Validation**: All inputs are validated using Pydantic models in `schema.py`. The `action` field acts as a discriminator to route payloads to the correct handler.
- **Channel Resolution**: A specialized internal helper, `_resolve_channel_id`, automatically maps flexible target identifiers for messaging operations (Channel Names like `#general`, Channel IDs, or User IDs like `U...`) to the correct Slack identifiers. For `upload_file`, Slack's external upload API requires a real conversation ID, so unresolved channel names are rejected before upload begins.
- **File Uploads**: Implements Slack's recommended 3-step external upload flow (`getUploadURLExternal`), supporting both absolute filesystem paths and base64-encoded content.
- **Authentication**: Bot tokens (`xoxb-...`) are resolved at call-time via the `SecretProvider`, ensuring no credentials are ever logged or stored on the instance.

### Available Operations

| Action | Description |
|---|---|
| `post_message` | Send a message to a channel, group, or user |
| `send_direct_message` | Send a private message to a specific user (resolves User ID to DM) |
| `upload_file` | Upload and share a file to a channel or direct message |

---

## 2. Operation Reference

### `post_message`

Sends a message to a Slack conversation. Supports plain text and rich Block Kit layouts.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"post_message"` |
| `channel` | string | ✅ | Target Channel ID (`C...`), Name (`#general`), or User ID (`U...`) |
| `message` | string | ✅ | Plain-text fallback message (markdown supported) |
| `blocks` | array / string | No | Block Kit payload as JSON string or pre-parsed list |
| `token_secret_key` | string | No | SecretProvider key (Default: `SLACK_BOT_TOKEN`) |

**Request body — with blocks:**

```json
{
  "action": "post_message",
  "channel": "#general",
  "message": "Hello from Node-Wire!",
  "blocks": [
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "Hello *Node-Wire*!" }
    }
  ]
}
```

---

### `send_direct_message`

A specialized action for private communication. If a User ID is provided, the connector automatically opens/resolves the DM channel before posting.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"send_direct_message"` |
| `channel` | string | ✅ | Target User ID (`U...`), Channel ID (`D...`), or Name |
| `message` | string | ✅ | Plain-text fallback message |
| `blocks` | array / string | No | Optional Block Kit payload |

**Request body:**

```json
{
  "action": "send_direct_message",
  "channel": "U12345678",
  "message": "Private clinical notification."
}
```

---

### `upload_file`

Uploads a file to Slack using the external-upload flow. Supports local file paths (sandboxed) or raw base64 data.
Unlike `chat.postMessage`, this Slack API requires a real conversation ID when sharing the uploaded file, so unresolved names like `#general` are not accepted.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"upload_file"` |
| `channel` | string | ✅ | Target Channel, Name, or User ID to share the file with |
| `filename` | string | No | Display name for the uploaded file |
| `initial_comment` | string | No | Message posted alongside the file |
| `filepath` | string | No* | Absolute path to local file (sandboxed). *Required if content_base64 is empty* |
| `content_base64` | string | No* | Base64-encoded content. *Required if filepath is empty* |

**Request body — via Filename:**

```json
{
  "action": "upload_file",
  "channel": "C12345678",
  "filename": "patient_summary.pdf",
  "filepath": "/slack_attachments/summary_123.pdf"
}
```

---

## 3. Error Taxonomy

All Slack API errors are mapped into the Node-Wire platform taxonomy. The connector translates `ok: false` responses into typed exceptions for consistent retry and troubleshooting.

| Error Code | Category | Trigger Conditions |
|---|---|---|
| `SLACK_AUTH_ERROR` | `AUTH` | Token invalid, revoked, or account inactive |
| `SLACK_PERMISSION_ERROR` | `AUTH` | Missing required OAuth scopes (e.g., `chat:write`) |
| `SLACK_RATE_LIMIT` | `RETRYABLE` | HTTP 429 or `ratelimited` error |
| `SLACK_UPLOAD_ERROR` | `BUSINESS` | Bad content, file too large, or upload step failure |
| `SLACK_MESSAGE_ERROR` | `BUSINESS` | Channel not found, invalid blocks, or other message errors |

### Implementation Note
The connector enforces a default upload limit (configurable via `NW_SLACK_UPLOAD_LIMIT_MB`) to prevent memory exhaustion during base64 decoding.
