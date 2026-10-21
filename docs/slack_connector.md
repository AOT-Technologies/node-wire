<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Slack connector

This document covers the Slack connector under `src/node_wire_slack` in two parts:

1. **[Slack bot setup](#slack-bot-setup)** — Create a Slack app, configure OAuth scopes, and obtain your bot token.
2. **[REST API reference](#rest-api-reference)** — Connector actions, request/response shapes, and flexible channel resolution.

For **MCP** (for example, ToolHive), tools are named `slack_<action>` from the connector manifest (for example, `slack_post_message`). Legacy dotted names (`slack.post_message`) still work on `tools/call` but are not what `tools/list` advertises.

---

## Slack bot setup

The Slack connector uses a **Bot User OAuth Token** to interact with your workspace.

### Prerequisites

- A Slack workspace where you have permission to install apps.
- [Slack API Dashboard](https://api.slack.com/apps) access.

### Step 1: Create a Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Select **From scratch**.
3. Give your app a name (for example, `Node-Wire Connector`) and select your workspace.
4. Click **Create App**.

### Step 2: Configure scopes

1. In the left sidebar, go to **OAuth & Permissions**.
2. Scroll down to **Scopes > Bot Token Scopes**.
3. Add the following scopes:
   - `chat:write` — Send messages to channels and DMs.
   - `files:write` — Upload and share files.
   - `im:write` — Start direct messages with users.
   - `groups:read` (optional) — If you need to post to private channels the bot is invited to.
   - `channels:read` (optional) — If you need to resolve channel names.

### Step 3: Install and get token

1. Scroll back up to the top of the **OAuth & Permissions** page.
2. Click **Install to Workspace**.
3. Click **Allow** to authorize the bot.
4. Copy the **Bot User OAuth Token** (it starts with `xoxb-`).

### Step 4: Configure the connector

Add the token to your `.env` file:

```env
SLACK_BOT_TOKEN=<your-bot-token>
```

Replace `<your-bot-token>` with the `xoxb-` token you copied in step 3.

### Step 5: Invite the bot (important)

Slack bots cannot "see" private channels unless they are explicitly invited.

1. Go to the Slack channel you want the bot to use.
2. Type `/invite @<your-app-name>`, replacing `<your-app-name>` with the name from step 1, and press Enter.

---

## REST API reference

The connector exposes actions as standard REST endpoints. For messaging actions, channel identifiers are flexible and automatically resolved.

### Operations overview

- Connector ID: `slack`
- Base REST path: `POST /connectors/slack/{action}`

### Actions

Request and response schemas come from the connector's Pydantic models and are published
live at `http://localhost:8000/docs` (Swagger UI) and `/openapi.json`; the same models
drive the MCP tool schemas. The connector-specific behaviour those schemas do **not**
capture follows.

#### `post_message`

Send a message to a channel, group, or user. Supports Slack `blocks`.

**Channel resolution** — the `channel` field accepts three forms:

| Form | Example | Behaviour |
|---|---|---|
| Channel name | `#general` | Passed to Slack, which resolves it by name |
| Channel ID | `C…`, `G…`, `D…`, `Z…` | Used directly |
| User ID | `U…`, `W…` | Automatically resolved to a DM channel |

#### `send_direct_message`

A specialized action for DMs. If targeted at a User ID, the connector ensures the DM
channel is open before posting.

#### `upload_file`

Uploads a file to a Slack channel or DM. The content may be supplied two ways:

| Field | Use |
|---|---|
| `content_base64` | Inline, base64-encoded file bytes |
| `filepath` | Absolute path on disk — **must** sit inside `NW_SLACK_ATTACHMENTS_DIR` (default `/slack_attachments`) |

Slack's external upload API needs a real conversation ID, so `channel` must be a channel ID or user ID, not a name such as `#general`. Uploads larger than `NW_SLACK_UPLOAD_LIMIT_MB` (default 50, maximum 100) are rejected with `SLACK_UPLOAD_ERROR`.

### Error taxonomy

| Category | Platform code | Cause |
|---|---|---|
| `AUTH` | `SLACK_AUTH_ERROR` | Invalid or revoked token |
| `AUTH` | `SLACK_PERMISSION_ERROR` | Missing OAuth scope |
| `RETRYABLE` | `SLACK_RATE_LIMIT` | Slack rate limit (429) |
| `BUSINESS` | `SLACK_MESSAGE_ERROR` | Channel not found or invalid payload |
| `BUSINESS` | `SLACK_UPLOAD_ERROR` | File too large or bad content |

---

### Related

- Pre-built per-connector Docker images: [Local wheels → images](local-packages-to-images.md)
- Connector architecture: [Build a connector — How connectors work](connectors-build.md#how-connectors-work)
