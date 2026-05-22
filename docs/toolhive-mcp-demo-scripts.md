<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# ToolHive MCP Demo Scripts

> **Local utilities to validate ToolHive → Node Wire Slack MCP with JWT auth and scope policy, without running the full agent.**

These scripts live under [`scripts/`](../scripts/) and are intended for development and manual smoke tests only.

| Script | Purpose |
|--------|---------|
| [`generate_demo_authz_tokens.py`](../scripts/generate_demo_authz_tokens.py) | Mint admin and restricted demo JWTs |
| [`call_toolhive_mcp.py`](../scripts/call_toolhive_mcp.py) | List tools and post a hardcoded Slack message |
| [`call_toolhive_mcp_upload_file.py`](../scripts/call_toolhive_mcp_upload_file.py) | Upload a hardcoded text file to Slack |

**Warning:** The default signing secret `node-wire-demo-authz-2026` is for local/dev only. Do not use it in production.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Recommended workflow](#recommended-workflow)
- [generate_demo_authz_tokens.py](#generate_demo_authz_tokenspy)
- [call_toolhive_mcp.py](#call_toolhive_mcppy)
- [call_toolhive_mcp_upload_file.py](#call_toolhive_mcp_upload_filepy)
- [Environment variables](#environment-variables)
- [Troubleshooting](#troubleshooting)
- [Related documentation](#related-documentation)

---

## Prerequisites

1. **Slack MCP server** running behind ToolHive (or direct streamable-HTTP), with a proxy URL such as `http://127.0.0.1:26608/mcp`.

2. **Server `.env`** (Node Wire Slack MCP container or local `MODE=MCP`):

   - `NW_ALLOWED_CONNECTORS` includes `slack`
   - `SLACK_BOT_TOKEN=xoxb-...`
   - MCP auth enabled and scope policy configured (see [Production authz baseline](mcp-servers.md#production-authz-baseline-recommended) in [mcp-servers.md](mcp-servers.md))

   Example after running the token generator:

   ```env
   NW_MCP_AUTH_ENABLED=true
   NW_MCP_JWT_SECRET=node-wire-demo-authz-2026
   NW_MCP_SCOPE_POLICY_DEFAULT=deny
   NW_MCP_ACTION_SCOPE_MAP_JSON={"slack.post_message":"mcp:slack.post_message","slack.send_direct_message":"mcp:slack.send_direct_message","slack.upload_file":"mcp:slack.upload_file"}
   SLACK_BOT_TOKEN=xoxb-your-bot-token
   ```

3. **Client env** (machine where you run the call scripts):

   ```env
   TOOLHIVE_MCP_URL=http://127.0.0.1:26608/mcp
   TOOLHIVE_MCP_BEARER_TOKEN=<admin-or-restricted-jwt>
   ```

   You can pass `--url` and `--token` instead of setting these variables.

4. **Python dependencies:** `httpx` and `PyJWT` (included when using `uv sync` / project venv). Run commands from the repository root:

   ```bash
   python scripts/generate_demo_authz_tokens.py
   python scripts/call_toolhive_mcp.py
   python scripts/call_toolhive_mcp_upload_file.py
   ```

---

## Recommended workflow

```mermaid
flowchart LR
  genTokens[generate_demo_authz_tokens.py]
  configureEnv[Copy tokens to .env]
  listAndPost[call_toolhive_mcp.py]
  uploadFile[call_toolhive_mcp_upload_file.py]
  genTokens --> configureEnv --> listAndPost --> uploadFile
```

1. Generate JWTs and copy the printed server `.env` lines into the Slack MCP server environment.
2. Set `TOOLHIVE_MCP_URL` and `TOOLHIVE_MCP_BEARER_TOKEN` (admin token) on the client.
3. Run `call_toolhive_mcp.py` to list tools and send the demo message.
4. Run `call_toolhive_mcp_upload_file.py` to upload the demo file (requires admin or upload scope).

To verify scope denial, repeat step 4 with the **restricted** token from the generator (`upload_file` should return `POLICY_DENIED`).

---

## generate_demo_authz_tokens.py

Prints ready-to-paste configuration and two HS256 JWTs signed with the demo secret.

### Output sections

| Section | Contents |
|---------|----------|
| MCP server (`.env`) | `NW_MCP_AUTH_ENABLED`, `NW_MCP_JWT_SECRET`, `NW_MCP_SCOPE_POLICY_DEFAULT`, `NW_MCP_ACTION_SCOPE_MAP_JSON` |
| Admin token | `scopes: ["*"]`, empty `blocked_scopes` — full Slack access |
| Restricted token | Messaging scopes only; `blocked_scopes: ["mcp:slack.upload_file"]` |

### CLI

| Flag | Default | Description |
|------|---------|-------------|
| `--secret` | `node-wire-demo-authz-2026` | HS256 signing secret (must match `NW_MCP_JWT_SECRET` on the server) |
| `--tenant-id` | `demo` | `tenant_id` JWT claim |

### Examples

```bash
python scripts/generate_demo_authz_tokens.py

python scripts/generate_demo_authz_tokens.py --secret node-wire-demo-authz-2026 --tenant-id demo
```

### Which token to use

| Script / action | Token |
|-----------------|-------|
| `call_toolhive_mcp.py` default demo post | Admin (or any token with `mcp:slack.post_message`) |
| `call_toolhive_mcp_upload_file.py` | **Admin** (or JWT with `mcp:slack.upload_file` and upload not in `blocked_scopes`) |
| Prove upload denial | Restricted token |

---

## call_toolhive_mcp.py

Minimal streamable-HTTP MCP client: `initialize`, optional `notifications/initialized`, then JSON-RPC `tools/list` and `tools/call`. Sends auth in HTTP headers (`Authorization`, `X-API-Key`) and in request `_meta` for gateway compatibility.

### Default behavior

When `--tool-name` is omitted:

1. **`tools/list`** — prints tool names and short descriptions (`OK: N tool(s)`).
2. **`slack.post_message`** — hardcoded demo:

   | Constant | Value |
   |----------|-------|
   | Tool | `slack.post_message` |
   | Channel | `bot-testing` |
   | Message | `hi` |

Use `--list-tools` to list only and skip the demo post.

### CLI

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `TOOLHIVE_MCP_URL` | MCP endpoint URL |
| `--token` | `TOOLHIVE_MCP_BEARER_TOKEN` or `TOOLHIVE_MCP_API_KEY` | Bearer/API token |
| `--timeout` | `60` | HTTP timeout (seconds) |
| `--list-tools` | off | List tools only; skip demo message |
| `--tool-name` | — | Call a specific tool instead of default flow |
| `--arguments-json` | `{}` | JSON object for `--tool-name` |
| `--show` | `20` | Max tools to print in list mode |

### Examples

```bash
# List + demo post (env vars)
python scripts/call_toolhive_mcp.py

# Explicit URL and token
python scripts/call_toolhive_mcp.py \
  --url http://127.0.0.1:26608/mcp \
  --token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# List only
python scripts/call_toolhive_mcp.py --list-tools

# Custom tool call
python scripts/call_toolhive_mcp.py \
  --tool-name slack.post_message \
  --arguments-json '{"channel":"C1234567890","message":"hello"}'
```

### SSE responses

ToolHive may return `text/event-stream` bodies (`data: {"jsonrpc":"2.0",...}`) even on HTTP 200. The script parses both plain JSON and SSE `data:` lines and matches JSON-RPC responses to the request `id` (including ToolHive suffixes like `uuid|s:session-id`).

A successful list shows `OK: 3 tool(s)` in the terminal while server logs may still print the full `tools/list` JSON-RPC payload — both indicate success.

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | MCP/HTTP error (`McpCallError`) |
| `2` | Missing URL, invalid `--arguments-json`, or conflicting flags |

---

## call_toolhive_mcp_upload_file.py

Imports `ToolHiveMcpCaller` from `call_toolhive_mcp.py` and invokes `slack.upload_file` with hardcoded content.

### Hardcoded upload

| Field | Value |
|-------|-------|
| Tool | `slack.upload_file` |
| Channel | `C0B4U96926A` (Slack channel ID) |
| Filename | `helloworld.txt` |
| Content | `helloworld` (sent as `content_base64`) |
| Initial comment | `Uploaded by call_toolhive_mcp_upload_file.py` |

Upload uses `content_base64`, not `filepath`. For path-based uploads, see [slack_connector.md](slack_connector.md).

### CLI

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `TOOLHIVE_MCP_URL` | MCP endpoint URL |
| `--token` | `TOOLHIVE_MCP_BEARER_TOKEN` or `TOOLHIVE_MCP_API_KEY` | Bearer/API token |
| `--timeout` | `120` | HTTP timeout (seconds) |

### Examples

```bash
python scripts/call_toolhive_mcp_upload_file.py

python scripts/call_toolhive_mcp_upload_file.py \
  --url http://127.0.0.1:26608/mcp \
  --token <admin-jwt>
```

### Exit codes

Same as `call_toolhive_mcp.py` (`0` / `1` / `2` where applicable).

---

## Environment variables

| Variable | Role |
|----------|------|
| `NW_MCP_JWT_SECRET` | Server: must match `--secret` used by the token generator |
| `NW_MCP_AUTH_ENABLED` | Server: enable JWT/API key checks on `tools/call` |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | Server: typically `deny` for explicit scopes |
| `NW_MCP_ACTION_SCOPE_MAP_JSON` | Server: maps `slack.*` actions to `mcp:slack.*` scopes |
| `SLACK_BOT_TOKEN` | Server: Slack Bot token for connector execution |
| `NW_ALLOWED_CONNECTORS` | Server: must include `slack` |
| `TOOLHIVE_MCP_URL` | Client: ToolHive proxy URL for both call scripts |
| `TOOLHIVE_MCP_BEARER_TOKEN` | Client: JWT or API key sent to MCP |
| `TOOLHIVE_MCP_API_KEY` | Client: alternate to bearer token |

Templates: [`sample.env`](../sample.env). Broader MCP auth notes: [mcp-servers.md](mcp-servers.md).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ERROR: MCP URL required` | Missing `TOOLHIVE_MCP_URL` | Set env or pass `--url` |
| `Non-JSON response (200): data: {...}` | Older script without SSE parser | Use current `call_toolhive_mcp.py` (parses SSE `data:` lines) |
| `MCP_AUTH_REQUIRED` / `MCP_AUTH_INVALID` on `tools/call` | Missing token, wrong secret, or expired JWT | Regenerate tokens; align `NW_MCP_JWT_SECRET` with generator `--secret` |
| `POLICY_DENIED` on upload | Restricted token or missing scope | Use admin token or add `mcp:slack.upload_file` to `scopes` without blocking it |
| `channel_not_found` (post_message) | Invalid channel name/ID for your workspace | Use a real channel ID or name the bot can access |
| Upload validation errors | Wrong arguments for `upload_file` | Channel must be ID (`C…`); use `content_base64` or sandboxed `filepath` per [slack_connector.md](slack_connector.md) |
| `Connection refused` | ToolHive proxy or MCP server not running | Confirm proxy URL in ToolHive UI matches `TOOLHIVE_MCP_URL` |

---

## Related documentation

- [mcp-servers.md](mcp-servers.md) — Slack MCP server, Docker image `nw-slack`, production auth baseline
- [slack_connector.md](slack_connector.md) — `upload_file`, attachments directory, API behavior
- [toolhive_agent_scenario.md](toolhive_agent_scenario.md) — Full ToolHive agent workflow (FHIR → Drive → email)
- [mcp.md](mcp.md) — MCP integration overview
- [sample.env](../sample.env) — Environment variable templates
