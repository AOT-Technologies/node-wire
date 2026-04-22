# Slack Connector

This document covers the Slack connector under `src/node_wire_slack` in two parts:

1. **[Slack Bot Setup](#slack-bot-setup)** — Create a Slack app, configure OAuth scopes, and obtain your bot token.
2. **[REST API Reference](#rest-api-reference)** — Connector actions, request/response shapes, and flexible channel resolution.

For **MCP** (e.g. ToolHive), tools are named `slack.<action>` from the connector manifest (e.g. `slack.post_message`).

---

## Slack Bot Setup

The Slack connector uses a **Bot User OAuth Token** to interact with your workspace.

### Prerequisites

- A Slack workspace where you have permission to install apps.
- [Slack API Dashboard](https://api.slack.com/apps) access.

### Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App**.
2. Select **From scratch**.
3. Give your app a name (e.g., `Node-Wire Connector`) and select your workspace.
4. Click **Create App**.

### Step 2: Configure Scopes

1. In the left sidebar, go to **OAuth & Permissions**.
2. Scroll down to **Scopes > Bot Token Scopes**.
3. Add the following scopes:
   - `chat:write` — Send messages to channels and DMs.
   - `files:write` — Upload and share files.
   - `im:write` — Start direct messages with users.
   - `conversations:open` — Resolve User IDs to DM channel IDs.
   - `groups:read` (optional) — If you need to post to private channels the bot is invited to.
   - `channels:read` (optional) — If you need to resolve channel names.

### Step 3: Install and Get Token

1. Scroll back up to the top of the **OAuth & Permissions** page.
2. Click **Install to Workspace**.
3. Click **Allow** to authorize the bot.
4. Copy the **Bot User OAuth Token** (it starts with `xoxb-`).

### Step 4: Configure the Connector

Add the token to your `.env` file:

```env
SLACK_BOT_TOKEN=xoxb-your-token-here
```

### Step 5: Invite the Bot (Important)

Slack bots cannot "see" private channels unless they are explicitly invited. 

1. Go to the Slack channel you want the bot to use.
2. Type `/invite @YourAppName` and press Enter.

---

## REST API Reference

The connector exposes actions as standard REST endpoints. Channel identifiers are flexible and automatically resolved.

### Operations overview

- Connector ID: `slack`
- Base REST path: `POST /connectors/slack/{action}`

### Actions

#### `post_message`

Send a message to a channel, group, or user.

**Request body:**

```json
{
  "channel": "#general",
  "message": "Clinical alert: Patient summary available.",
  "blocks": [
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "*Emergency Update*: BP 180/110" }
    }
  ]
}
```

**Channel Resolution:**
- **Channel Name**: Starts with `#` (e.g., `#general`).
- **Channel ID**: Starts with `C` or `G` (e.g., `C12345`).
- **User ID**: Starts with `U` or `W` (e.g., `U12345`). Automatically resolved to a DM channel.

#### `send_direct_message`

A specialized action for DMs. If targeted at a User ID, the connector ensures the DM channel is open before posting.

**Request body:**

```json
{
  "channel": "U12345678",
  "message": "You have a new lab result to review."
}
```

#### `upload_file`

Uploads a file to a Slack channel or DM.

**Request body (Base64):**

```json
{
  "channel": "C12345678",
  "filename": "labs.pdf",
  "content_base64": "JVBER...",
  "initial_comment": "Here is the PDF summary."
}
```

**Request body (Filesystem):**

```json
{
  "channel": "U12345678",
  "filename": "summary.pdf",
  "filepath": "/slack_attachments/p_123.pdf"
}
```

> **Note:** `filepath` must be within the directory defined by `NW_SLACK_ATTACHMENTS_DIR` (default `/slack_attachments`).

### Error Taxonomy

| Category | Platform Code | Cause |
|---|---|---|
| `AUTH` | `SLACK_AUTH_ERROR` | Invalid or revoked token |
| `AUTH` | `SLACK_PERMISSION_ERROR` | Missing OAuth scope |
| `RETRYABLE` | `SLACK_RATE_LIMIT` | Slack rate limit (429) |
| `BUSINESS` | `SLACK_MESSAGE_ERROR` | Channel not found or invalid payload |
| `BUSINESS` | `SLACK_UPLOAD_ERROR` | File too large or bad content |

---

### Related

- Individual MCP Servers: [docs/mcp-servers.md](mcp-servers.md)
- Connector Architecture: [docs/connectors.md](connectors.md)
