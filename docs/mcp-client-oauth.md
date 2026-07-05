<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# MCP Client OAuth (Outbound)

Node Wire can act as an **OAuth 2.1 client** when connecting to **remote HTTP MCP servers** that require authorization per the MCP Authorization specification (2025-11-25).

This is **separate** from:

- **Inbound MCP server auth** (`NW_MCP_API_KEY`, `NW_MCP_JWT_SECRET`) — clients calling *node-wire’s* MCP server.
- **Connector OAuth** (`OAuth2AuthProvider`) — Epic/Cerner/Stripe credentials for connector actions.

## Package

Implementation lives in `src/node_wire_runtime/mcp_client/`:

| Module | Purpose |
|--------|---------|
| `config.py` | Operator settings (Section 10) |
| `discovery.py` | RFC 9728 + RFC 8414 metadata discovery |
| `dcr.py` | RFC 7591 dynamic client registration |
| `oauth_flow.py` | Authorization code + PKCE + resource parameter |
| `redirect_listener.py` | Loopback redirect callback (desktop) |
| `storage.py` | Persisted DCR registrations per issuer |

## Configuration

```python
from node_wire_runtime.mcp_client import McpClientConfig, McpServerConfig, AuthConfig

config = McpClientConfig(
    server=McpServerConfig(url="https://mcp.example.com/mcp"),
    auth=AuthConfig(
        scopes="mcp:tools",
        client=AuthClientConfig(id="", secret=""),  # empty → use DCR when supported
    ),
)
```

### Section 10 settings

| Setting | Default | Purpose |
|---------|---------|---------|
| `server.url` | (required) | Canonical MCP server URL; RFC 8707 `resource` |
| `auth.discovery.cacheTtlSeconds` | `3600` | Discovery metadata cache TTL |
| `auth.dcr.enabled` | `true` | Attempt RFC 7591 when `registration_endpoint` exists |
| `auth.client.id` / `secret` | empty | Override when AS has no DCR |
| `auth.scopes` | empty | Requested scopes |
| `auth.production` | `false` | When `true`, require `https://` MCP server URL and `configured-url` HTTPS redirect; disables HTTP loopback |
| `auth.redirect.mode` | `loopback` | `loopback` or `configured-url` (defaults to `configured-url` when `auth.production=true`) |
| `auth.redirect.url` | `http://127.0.0.1:0/callback` | HTTPS callback URL for hosted / production mode |
| `auth.token.refreshLeadSeconds` | `60` | Proactive refresh lead time |
| `auth.token.store` | `os-keychain` | Token storage backend |

## Discovery

```python
from node_wire_runtime.mcp_client import discover, discovery_cache_for_config

result = await discover(config, www_authenticate=challenge_header, cache=cache)
```

Triggered on first MCP request without a token or after `401` with `WWW-Authenticate` `resource_metadata`.

## Authorization

**Loopback (desktop):**

```python
from node_wire_runtime.mcp_client import AuthorizationCodeFlow

flow = AuthorizationCodeFlow(config)
tokens = await flow.run_loopback_authorization(open_browser=True)
```

**Configured URL (hosted / production):**

```python
url = await flow.start_authorization(open_browser=True)
# User completes login; your app receives redirect at auth.redirect.url
tokens = await flow.complete_authorization_with_callback_url(callback_url)
```

### Production hardening (`auth.production=true`)

Set `NW_MCP_OAUTH_PRODUCTION=true` (or `auth.production=True` in code) for server deployments:

- `mcp.server.url` must use `https://`
- `auth.redirect.mode` must be `configured-url`
- `auth.redirect.url` must use `https://` (HTTP loopback is rejected)
- Re-authorization cannot use `run_loopback_authorization`; the host app must expose an HTTPS callback route or inject `TokenManager(reauthorize=...)` / `McpOAuthClient(reauthorize=...)`

Example environment (see also `sample.env`):

```env
NW_MCP_OAUTH_ENABLED=true
NW_MCP_OAUTH_PRODUCTION=true
NW_MCP_SERVER_URL=https://mcp.example.com/mcp
NW_MCP_OAUTH_REDIRECT_MODE=configured-url
NW_MCP_OAUTH_REDIRECT_URL=https://app.example.com/oauth/mcp/callback
```

Desktop / local development keeps the default (`NW_MCP_OAUTH_PRODUCTION` unset or `false`) with `auth.redirect.mode=loopback`.

## STDIO

STDIO MCP transports use environment credentials only; this OAuth client does not apply.

## Token manager and HTTP client (Phases 4–5)

```python
from node_wire_runtime.mcp_client import McpOAuthClient, create_http_mcp_client

# Factory: NW_MCP_OAUTH_ENABLED=true, or legacy TOOLHIVE_MCP_BEARER_TOKEN for static auth
client = create_http_mcp_client("https://mcp.example.com/mcp")

# Production: pass reauthorize when tokens must be renewed without loopback
# client = create_http_mcp_client("https://mcp.example.com/mcp", reauthorize=my_reauth_fn)

tools = await client.list_tools()
```

Environment variables: see `sample.env` (`NW_MCP_OAUTH_*`).

## ToolHive / playground integration (Phase 6)

`agents.toolhive` and `playground/scenarios.py` call `create_http_mcp_client()` for HTTP MCP URLs:

1. `TOOLHIVE_MCP_BEARER_TOKEN` / `TOOLHIVE_MCP_API_KEY` → legacy static Bearer (no OAuth).
2. `NW_MCP_OAUTH_ENABLED=true` → `McpOAuthClient` with discovery, DCR, PKCE, token refresh.

## Internal demo script

```bash
uv run python scripts/demo_mcp_oauth_mock.py
```
