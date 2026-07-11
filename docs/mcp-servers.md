<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Node Wire — Individual MCP Servers

This document covers everything needed to build, run, configure, and integrate the per-connector MCP servers with ToolHive and the Agentic Workflow.

---

## Table of contents

- [Architecture](#architecture)
- [Naming conventions](#naming-conventions)
- [Shifting between transport modes](#shifting-between-transport-modes)
- [Testing with MCP Inspector](#testing-with-mcp-inspector)
- [Environment configuration](#environment-configuration)
- [Build images](#build-images)
- [Run with docker-compose](#run-with-docker-compose)
- [ToolHive registration](#toolhive-registration)
- [Agentic workflow integration](#agentic-workflow-integration)
- [Local testing (no ToolHive required)](#local-testing-no-toolhive-required)
- [Troubleshooting](#troubleshooting)

---

## Architecture

Each connector is deployed as an independent MCP server (Docker image). The Agentic Workflow connects to all of them simultaneously via ToolHive proxy URLs and merges their tools into a single tool list.

<div class="nw-diagram" markdown="1">

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "primaryColor": "#242930",
    "primaryTextColor": "#E8EDF5",
    "primaryBorderColor": "#37c4f0",
    "lineColor": "#62d2f5",
    "fontFamily": "Inter, system-ui, sans-serif",
    "fontSize": "13px"
  },
  "flowchart": {
    "curve": "basis",
    "padding": 24,
    "nodeSpacing": 35,
    "rankSpacing": 50,
    "diagramPadding": 20
  }
}}%%
flowchart TB
    Agent["Agentic Workflow"]

    subgraph toolhive ["ToolHive · MCP proxy layer"]
        direction LR
        GDrive["nw-google-drive"]
        Epic["nw-smartonfhir-epic"]
        Cerner["nw-smartonfhir-cerner"]
        SMTP["nw-smtp"]
        Stripe["nw-stripe"]
        Salesforce["nw-salesforce"]
        Slack["nw-slack"]
    end

    Agent -->|"TOOLHIVE_MCP_URLS"| GDrive
    Agent -->|"TOOLHIVE_MCP_URLS"| Epic
    Agent -->|"TOOLHIVE_MCP_URLS"| Cerner
    Agent -->|"TOOLHIVE_MCP_URLS"| SMTP
    Agent -->|"TOOLHIVE_MCP_URLS"| Stripe
    Agent -->|"TOOLHIVE_MCP_URLS"| Salesforce
    Agent -->|"TOOLHIVE_MCP_URLS"| Slack

    classDef agent fill:#1a3a4a,stroke:#37c4f0,stroke-width:2px,color:#E8EDF5
    classDef server fill:#3a2430,stroke:#e01d5a,stroke-width:2px,color:#E8EDF5

    class Agent agent
    class GDrive,Epic,Cerner,SMTP,Stripe,Salesforce,Slack server

    style toolhive fill:#151920,stroke:#ecb32e,stroke-width:2px,color:#ecb32e
```

</div>

---

## Naming conventions

| Connector | Python entrypoint | Docker image | ToolHive name | MCP tools exposed |
|---|---|---|---|---|
| Google Drive | `python -m agents.google_drive_mcp` | `nw-google-drive` | `nw-google-drive` | All manifest actions for `google_drive` (names `google_drive.<action>`, e.g. `google_drive.files.upload`) |
| SMART on FHIR (Epic) | `python -m agents.fhir_epic_mcp` | `nw-smartonfhir-epic` | `nw-smartonfhir-epic` | All manifest actions for `fhir_epic` (e.g. `fhir_epic.read_patient`) |
| SMART on FHIR (Cerner) | `python -m agents.fhir_cerner_mcp` | `nw-smartonfhir-cerner` | `nw-smartonfhir-cerner` | All manifest actions for `fhir_cerner` (e.g. `fhir_cerner.read_patient`) |
| SMTP | `python -m agents.smtp_mcp` | `nw-smtp` | `nw-smtp` | `smtp.send_email` |
| Stripe | `python -m agents.stripe_mcp` | `nw-stripe` | `nw-stripe` | All manifest actions for `stripe` (e.g., `stripe.charge`) |
| Salesforce | `python -m agents.salesforce_mcp` | `nw-salesforce` | `nw-salesforce` | All manifest actions for `salesforce` (e.g., `salesforce.create_lead`) |
| Slack | `python -m agents.slack_mcp` | `nw-slack` | `nw-slack` | All manifest actions for `slack` (e.g. `slack.post_message`) |

### Adding a row for a new connector

When you add a standalone MCP server for a connector, add a row to the table above and update these files:

| Field | Convention |
|-------|--------------|
| Python entrypoint | `python -m agents.<connector_id>_mcp` (or `uv run nw-<kebab-name>` from root `pyproject.toml` `[project.scripts]`) |
| Docker image | `nw-<kebab-name>` |
| ToolHive name | Same as Docker image tag |
| MCP tools | `<connector_id>.<action>` for each manifest action |

Files to update: `src/agents/<name>_mcp.py`, root `pyproject.toml` `[project.scripts]`, `docker/<name>/Dockerfile`, `scripts/build-mcp-images.sh`, `docker-compose.mcp.yml`, this table (naming conventions + architecture diagram), and [local-packages-to-images.md](local-packages-to-images.md) (wheel requirements table). See [packaging.md — Adding a new publishable connector](packaging.md#adding-a-new-publishable-connector) for the full checklist.

#### Per-connector MCP entrypoint template (`src/agents/<name>_mcp.py`)

Every per-connector MCP file follows the same pattern — create `src/agents/<connector_id>_mcp.py`:

```python
# src/agents/<connector_id>_mcp.py
"""MCP Server — <Name> connector only. Usage: python -m agents.<connector_id>_mcp"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.<connector_id>_mcp")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    logger.info("Starting nw-<name> MCP server (stdio, manifest-driven)")
    McpServer(
        server_name="nw-<name>",
        connector_ids=["<connector_id>"],
    ).run_stdio()


if __name__ == "__main__":
    main()
```

`McpServer` is the shared manifest-driven server from `bindings.mcp_server.server`. The `connector_ids` list filters the full connector registry to only the connectors this image should expose. `run_stdio()` starts the MCP stdio transport; call `run_streamable_http()` instead for the HTTP transport (reads `NW_MCP_HOST`/`NW_MCP_PORT`/`NW_MCP_PATH`).

Then register the script in root `pyproject.toml`:

```toml
[project.scripts]
nw-<name> = "agents.<connector_id>_mcp:main"
```

#### Dockerfile template (`docker/<name>/Dockerfile`)

All per-connector Docker images follow the same structure. The key requirements are: copy `src/` and `config/` from the repo root, install pre-built wheels from `/wheels/`, pin `NW_ALLOWED_CONNECTORS` to the single connector ID, and run as a non-root user.

```dockerfile
FROM python:3.12-slim@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

LABEL org.opencontainers.image.title="nw-<name>" \
      org.opencontainers.image.description="Node Wire — <Name> MCP server" \
      org.opencontainers.image.source="https://github.com/AOT-Technologies/node-wire"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY src/ ./src/
COPY config/ ./config/
COPY packages/runtime/dist/*.whl /wheels/
COPY packages/connectors/<name>/dist/*.whl /wheels/

ENV PYTHONPATH=/app/src \
    NW_ALLOWED_CONNECTORS=<connector_id>

RUN pip install --no-cache-dir --find-links=/wheels \
    node-wire-runtime node-wire-<name> "mcp>=1.6.0" \
    && rm -rf /wheels

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app app \
    && chown -R app:app /app

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
    python -c "from agents.<connector_id>_mcp import main; assert callable(main); print('ok')" || exit 1

CMD ["python", "-m", "agents.<connector_id>_mcp"]
```

> **Note:** The `COPY packages/connectors/<name>/dist/*.whl` line requires the connector's publishable wheel (Tier 2) to be built first via `bash scripts/build-packages.sh packages/connectors/<name>`. Build wheels before building Docker images.


The unified server (`python -m agents.mcp_entrypoint`) exposes **every** connector enabled for MCP in `config/connectors.yaml` (e.g. `http_generic.request`, `stripe.charge`, `stripe.create_payment_intent`, `stripe.create_subscription`, `stripe.cancel_subscription`, `stripe.issue_refund`, plus the rows above).

### Tool arguments and security

- Tool name (`<connector_id>.<action>`) determines the routed action; do not rely on a separate `action` field in the JSON body to select a different operation.
- Per-action normalizers in `src/node_wire_runtime/mcp_normalizers.py` map common LLM mistakes to canonical schema fields; see also `src/node_wire_runtime/ingress.py` for shared MCP/REST behavior.
- **`tools/list` input schemas** omit the `action` field (manifest contract v2+). Pass only the fields shown in `inputSchema`; the server injects `action` from the tool name.

**Legacy rollout (Google Drive `google_drive.files.upload` only):**

| Variable | Values | Purpose |
|----------|--------|---------|
| `NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD` | `warn` (default), `allow` (same mapping, no deprecation log), `reject` | Legacy payload `action: "upload"` for `google_drive.files.upload`. Use `reject` once all clients omit `action` or use canonical `files.upload` only in pre-invoke validation paths. |

---

## Command reference

| Goal | Command |
|------|---------|
| Combined MCP server (all MCP-enabled connectors) | `uv run python -m agents.mcp_entrypoint` |
| Single-connector MCP | `uv run nw-<connector>` or `python -m agents.<connector>_mcp` |
| REST API | `uv run node-wire` (default `MODE=API`) |
| gRPC | `MODE=GRPC uv run node-wire` |

For MCP transport configuration (`NW_MCP_TRANSPORT`, `streamable-http`, Inspector), see **[mcp.md](mcp.md)** (canonical MCP transport doc). For REST/gRPC setup see **[installation.md](installation.md)**.

> **Note:** `uv run node-wire` and `python -m bindings_entrypoint` start the **REST API** by default (`MODE=API`). They do **not** start an MCP transport server. `MODE=MCP` on `bindings_entrypoint` is a POC stub only — use `agents.mcp_entrypoint` for real MCP.

---

## Shifting between transport modes

Node Wire now supports two ways to expose tools to AI agents. By default, it uses `stdio`, but you can easily shift to the native `streamable-http` mode for web-native deployments.

### Comparison: stdio vs. streamable-http

| Feature | stdio (Default) | streamable-http |
|---|---|---|
| **Best For** | ToolHive, local development, subprocess-based clients | Direct web integration, persistent servers, remote MCP clients |
| **Connectivity** | Standard input/output | HTTP POST plus server streaming support |
| **Port Management** | Not applicable | Requires an open port (default: 8081) |
| **Playground behavior** | Buffered agent response after the backend finishes | Tool steps appear as they complete; final answer streams into the UI |

### How to configure and shift modes

You can switch modes and ports instantly using environment variables. No code changes are required.

#### 1. Running in stdio mode (Default)
No extra variables are needed. This is the mode expected by local stdio clients and ToolHive-style stdio wrapping.

```bash
# Using uv
uv run python -m agents.mcp_entrypoint

# Using python
python -m agents.mcp_entrypoint

# Per-connector: uv run nw-slack
```

PowerShell:

```powershell
$env:NW_MCP_TRANSPORT="stdio"
# Using uv
uv run python -m agents.mcp_entrypoint

# Using python
python -m agents.mcp_entrypoint
```

#### 2. Shifting to native HTTP mode (Port 8081)
To run as a standalone HTTP server on port 8081:

**PowerShell (Windows):**
```powershell
$env:NW_MCP_TRANSPORT="streamable-http"
$env:NW_MCP_HOST="127.0.0.1"
$env:NW_MCP_PORT="8081"
$env:NW_MCP_PATH="/mcp"
# Using uv
uv run python -m agents.mcp_entrypoint

# Using python
python -m agents.mcp_entrypoint
```

**Bash (Linux/macOS):**
```bash
export NW_MCP_TRANSPORT="streamable-http"
export NW_MCP_HOST="127.0.0.1"
export NW_MCP_PORT="8081"
export NW_MCP_PATH="/mcp"
# Using uv
uv run python -m agents.mcp_entrypoint

# Using python
python -m agents.mcp_entrypoint
```

The native HTTP endpoint will be:

```text
http://127.0.0.1:8081/mcp
```

### Protocol-level requirements
When running in `streamable-http` mode, clients must comply with the strict MCP Streamable-HTTP specification:
- **Headers**: Clients must send `Accept: application/json, text/event-stream` on all requests.
- **Handshake**: The server will respond with a `Mcp-Session-Id` header which must be forwarded in all subsequent messages for that session.
- **Auth boundary**: Node Wire enforces MCP auth at the HTTP edge for the streamable endpoint (`/mcp`) before MCP handler dispatch. Missing/invalid credentials are rejected early with 401/403/503.

### Production authz baseline (recommended)

Use these settings for production-style posture:

```env
# MCP auth is ENABLED by default — do NOT set NW_MCP_AUTH_DISABLED in production.
# (Set NW_MCP_AUTH_DISABLED=true only for local development.)
NW_MCP_API_KEY=replace-with-strong-random-value
NW_MCP_SCOPE_POLICY_DEFAULT=deny
# Optional guardrail: fail startup if scope policy would be disabled
NW_MCP_SCOPE_POLICY_STRICT=true
```

Notes:
- MCP authentication is enforced unless `NW_MCP_AUTH_DISABLED=true` (the flag mirrors
  `NW_REST_AUTH_DISABLED` / `NW_GRPC_AUTH_DISABLED`). The legacy `NW_MCP_AUTH_ENABLED`
  flag is deprecated; it now honours its literal meaning (`=false` disables auth) and
  logs a deprecation warning. Prefer `NW_MCP_AUTH_DISABLED`.
- Code default is `deny` when `NW_MCP_SCOPE_POLICY_DEFAULT` is unset (fail-closed).
- `NW_MCP_SCOPE_POLICY_DEFAULT=deny` enforces fallback scope `mcp:<connector>.<action>` even when no explicit action map is present.
- Keep `NW_MCP_ACTION_SCOPE_MAP_JSON` for custom scope names across tools.
- API keys with `NW_MCP_API_KEY_SCOPES=*` are super-user keys by design and bypass per-action scope checks.
- JWT auth (`NW_MCP_JWT_SECRET`): set shared `NW_JWT_AUDIENCE` and `NW_JWT_ISSUER`; tokens must include `exp`, `iat`, `aud`, and `iss`.

### Playground transport indicator

The browser playground reads `/scenarios/agent-transport` and displays the current mode in the Agentic Workflow panel:

- `Transport: stdio`: chat uses the buffered `/scenarios/agent-chat` endpoint. Tool cards and the final answer appear after the backend agent run completes.
- `Transport: Streamable HTTP`: chat uses `/scenarios/agent-chat-stream`. Tool cards appear as each MCP tool finishes, and the final answer is appended to the assistant bubble as streamed chunks.

If you switch `NW_MCP_TRANSPORT`, restart the API server and hard refresh the browser so the latest `app.js` is loaded.

---

## Testing with MCP Inspector

MCP Inspector is the official browser-based developer tool for testing and debugging MCP servers. It runs with `npx` and opens a local UI, usually at `http://localhost:6274`.

### Inspect stdio mode

Use stdio mode when you want Inspector to launch the Python MCP server process itself:

```powershell
$env:NW_MCP_TRANSPORT="stdio"
npx @modelcontextprotocol/inspector uv run python -m agents.mcp_entrypoint
```

Per-connector examples:

```powershell
npx @modelcontextprotocol/inspector uv run nw-google-drive
npx @modelcontextprotocol/inspector uv run nw-smartonfhir-epic
npx @modelcontextprotocol/inspector uv run nw-smartonfhir-cerner
npx @modelcontextprotocol/inspector uv run python -m agents.smtp_mcp
```

In the Inspector UI:

1. Select `stdio` transport if it is not already selected.
2. Click `Connect`.
3. Open the `Tools` tab.
4. Click `List Tools`.
5. Pick a safe tool and run it with valid JSON arguments.

### Inspect streamable-http mode

Start the MCP server first:

```powershell
$env:NW_MCP_TRANSPORT="streamable-http"
$env:NW_MCP_HOST="127.0.0.1"
$env:NW_MCP_PORT="8081"
$env:NW_MCP_PATH="/mcp"
python -m agents.mcp_entrypoint
```

Then start Inspector in another terminal:

```powershell
npx @modelcontextprotocol/inspector
```

In the Inspector UI:

1. Set transport type to `Streamable HTTP`.
2. Set URL to `http://127.0.0.1:8081/mcp`.
3. Click `Connect`.
4. Open `Tools`.
5. Click `List Tools`.
6. Run a tool call with valid arguments.

For reusable client config, a streamable HTTP server entry should look like:

```json
{
  "type": "streamable-http",
  "url": "http://127.0.0.1:8081/mcp"
}
```

---

## Environment configuration

Copy `sample.env` to `.env` and fill in the sections for the servers you plan to run. Each server only reads the env vars from its own section — you do not need to configure all connectors.

```bash
cp sample.env .env
```

### Shared Required Variables

| Variable | Description |
|---|---|
| `NW_ALLOWED_CONNECTORS` | **Required.** Comma-separated list of allowed connector names (e.g. `fhir_epic,google_drive`). Individual servers still check this allowlist before loading. |

### Per-server required variables

#### `nw-google-drive`

| Variable | Description |
|---|---|
| `GOOGLE_DRIVE_SA_JSON` | Absolute path to the service account JSON key file **or** the full JSON content as a string |
| `GOOGLE_DRIVE_FOLDER_ID` | Drive folder ID (from the URL: `.../folders/<ID>`) |

```env
GOOGLE_DRIVE_SA_JSON=/absolute/path/to/service-account.json
GOOGLE_DRIVE_FOLDER_ID=your-google-drive-folder-id
```

> **ToolHive note:** When running inside ToolHive, set `GOOGLE_DRIVE_SA_JSON` to the JSON *contents* (not a file path) because ToolHive injects secrets as string values, not files.

#### `nw-smartonfhir-epic`

| Variable | Description |
|---|---|
| `EPIC_FHIR_BASE_URL` | Epic FHIR R4 base URL |
| `EPIC_TOKEN_URL` | Epic OAuth2 token endpoint |
| `EPIC_CLIENT_ID` | Your registered application client ID |
| `EPIC_KID` | Key ID that matches the public key registered in Epic |
| `EPIC_PRIVATE_KEY` | RSA private key (PEM format, `\n`-escaped for single-line env var) |

```env
EPIC_FHIR_BASE_URL=https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
EPIC_TOKEN_URL=https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token
EPIC_CLIENT_ID=your-epic-client-id
EPIC_KID=your-epic-kid
EPIC_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

Register your application at [Epic App Orchard](https://appmarket.epic.com/) or your organization's Epic sandbox.

#### `nw-smartonfhir-cerner`

| Variable | Description |
|---|---|
| `CERNER_FHIR_BASE_URL` | Cerner FHIR R4 base URL (includes tenant ID) |
| `CERNER_TOKEN_URL` | Cerner OAuth2 token endpoint (includes tenant ID) |
| `CERNER_CLIENT_ID` | Your registered application client ID |
| `CERNER_KID` | Key ID that matches the JWKS registered in Cerner |
| `CERNER_PRIVATE_KEY` | RSA private key (PEM format, `\n`-escaped for single-line env var) |
| `CERNER_SCOPES` | Space-separated SMART scopes required by your app |

```env
CERNER_FHIR_BASE_URL=https://fhir-ehr-code.cerner.com/r4/your-tenant-id
CERNER_TOKEN_URL=https://authorization.cerner.com/tenants/your-tenant-id/protocols/oauth2/profiles/smart-v1/token
CERNER_CLIENT_ID=your-cerner-client-id
CERNER_KID=your-cerner-kid
CERNER_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
CERNER_SCOPES="system/Patient.read system/Encounter.read system/DocumentReference.read system/DocumentReference.write"
```

Register your application at the [Cerner Developer Portal](https://code.cerner.com/).

#### `nw-smtp`

The SMTP MCP server exposes one tool: `smtp.send_email`. Tool input is **`to`**, **`subject`**, **`body`**, and optional **`from_email`** only — relay settings cannot be passed in the payload. When running under ToolHive, inject these as secrets:

| Variable | Description |
|---|---|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | Port: `587` (STARTTLS) or `465` (implicit TLS) |
| `SMTP_USE_TLS` | Whether to use STARTTLS when on port 587 (default: `true`) |
| `SMTP_USERNAME` | Login username (usually your email address) |
| `SMTP_PASSWORD` | Login password (use an App Password for Gmail) |
| `FROM_EMAIL` | Optional sender address override (defaults to `SMTP_USERNAME`) |

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
FROM_EMAIL=your-email@gmail.com
```

#### `nw-stripe`

| Variable | Description |
|---|---|
| `STRIPE_API_KEY` | Your Stripe secret API key (starts with `sk_test_` or `sk_live_`) |

```env
STRIPE_API_KEY=sk_test_your_secret_key_here
```

#### `nw-salesforce`

| Variable | Description |
|---|---|
| `SALESFORCE_INSTANCE_URL` | Your Salesforce instance URL (e.g., `https://domain.my.salesforce.com`) |
| `SALESFORCE_TOKEN_URL` | OAuth2 token endpoint (usually `https://login.salesforce.com/services/oauth2/token`) |
| `SALESFORCE_CLIENT_ID` | Connected App Client ID |
| `SALESFORCE_CLIENT_SECRET` | Connected App Client Secret |
| `SALESFORCE_REFRESH_TOKEN` | Refresh token with `refresh_token` and `api` scopes |

```env
SALESFORCE_INSTANCE_URL=https://nodenet.my.salesforce.com
SALESFORCE_TOKEN_URL=https://login.salesforce.com/services/oauth2/token
SALESFORCE_CLIENT_ID=your-client-id
SALESFORCE_CLIENT_SECRET=your-client-secret
SALESFORCE_REFRESH_TOKEN=your-refresh-token
```


#### `nw-slack`

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token (`xoxb-...`) |
| `NW_SLACK_ATTACHMENTS_DIR` | Optional: sandboxed directory for uploads (default: `/slack_attachments`) |

```env
SLACK_BOT_TOKEN=xoxb-your-bot-token
NW_SLACK_ATTACHMENTS_DIR=/slack_attachments
```

### ToolHive / Agent settings

| Variable | Description |
|---|---|
| `TOOLHIVE_MCP_URL` | Single MCP proxy URL (backward compatible) |
| `TOOLHIVE_MCP_URLS` | Comma-separated list of proxy URLs for multi-server deployments |
| `LLM_PROVIDER` | `groq` \| `openai` \| `gemini` \| `anthropic` (default: `groq`) |
| `GROQ_API_KEY` | API key for Groq (fast, free tier available) |
| `OPENAI_API_KEY` | API key for OpenAI |
| `GEMINI_API_KEY` | API key for Google Gemini |
| `ANTHROPIC_API_KEY` | API key for Anthropic Claude |

```env
# Multi-server (preferred when running per-connector images)
TOOLHIVE_MCP_URLS=http://localhost:7977/mcp,http://localhost:7978/mcp,http://localhost:7979/mcp

# Single server (backward compatible)
TOOLHIVE_MCP_URL=http://localhost:7977/mcp

LLM_PROVIDER=groq
GROQ_API_KEY=your-groq-api-key
```

---

## Build images

Before building images, build local wheels first. See [docs/local-packages-to-images.md](local-packages-to-images.md) for the full package -> image workflow and required wheel artifacts per image.

All MCP server images are built from the repository root using the automation script:

```bash
./scripts/build-mcp-images.sh
```

To tag with a specific version (defaults to the version in `pyproject.toml`):

```bash
./scripts/build-mcp-images.sh --version 0.1.0
```

This produces images tagged as both `latest` and the version string:

| Image name | Tags |
|---|---|
| `nw-google-drive` | `nw-google-drive:latest`, `nw-google-drive:0.1.0` |
| `nw-smartonfhir-epic` | `nw-smartonfhir-epic:latest`, `nw-smartonfhir-epic:0.1.0` |
| `nw-smartonfhir-cerner` | `nw-smartonfhir-cerner:latest`, `nw-smartonfhir-cerner:0.1.0` |
| `nw-smtp` | `nw-smtp:latest`, `nw-smtp:0.1.0` |
| `nw-stripe` | `nw-stripe:latest`, `nw-stripe:0.1.0` |
| `nw-salesforce` | `nw-salesforce:latest`, `nw-salesforce:0.1.0` |
| `nw-slack` | `nw-slack:latest`, `nw-slack:0.1.0` |

To build a single image manually from the repo root:

```bash
# Google Drive only
docker build -f docker/google-drive/Dockerfile -t nw-google-drive:latest .

# Epic FHIR only
docker build -f docker/fhir-epic/Dockerfile -t nw-smartonfhir-epic:latest .

# Cerner FHIR only
docker build -f docker/fhir-cerner/Dockerfile -t nw-smartonfhir-cerner:latest .

# SMTP only
docker build -f docker/smtp/Dockerfile -t nw-smtp:latest .

# Stripe only
docker build -f docker/stripe/Dockerfile -t nw-stripe:latest .

# Salesforce only
docker build -f docker/salesforce/Dockerfile -t nw-salesforce:latest .

# Slack only
docker build -f docker/slack/Dockerfile -t nw-slack:latest .
```

> **Note:** The build context must be the repository root (`.`) so the `COPY src/` and `COPY config/` instructions resolve correctly.

---

## Run with docker-compose

`docker-compose.mcp.yml` starts all MCP servers as stdio containers in one command. This is useful for local validation before configuring ToolHive.
Each service pins `NW_ALLOWED_CONNECTORS` to its own connector so a broad value in `.env` does not make per-connector images import optional dependencies they do not contain.

```bash
# Ensure local wheels exist and your .env is populated, then:
docker compose -f docker-compose.mcp.yml up --build
```

To start only a specific server:

```bash
docker compose -f docker-compose.mcp.yml up --build nw-smartonfhir-epic
```

---

## ToolHive registration

ToolHive wraps each stdio MCP server in an HTTP proxy and manages secret injection and container lifecycle. Run `thv run` once per connector — ToolHive assigns a port and keeps the container running.

```bash
# Google Drive
thv run --name nw-google-drive --transport stdio \
  --secret GOOGLE_DRIVE_SA_JSON,target=GOOGLE_DRIVE_SA_JSON \
  --secret GOOGLE_DRIVE_FOLDER_ID,target=GOOGLE_DRIVE_FOLDER_ID \
  nw-google-drive:latest

# Epic FHIR
thv run --name nw-smartonfhir-epic --transport stdio \
  --secret EPIC_FHIR_BASE_URL,target=EPIC_FHIR_BASE_URL \
  --secret EPIC_TOKEN_URL,target=EPIC_TOKEN_URL \
  --secret EPIC_CLIENT_ID,target=EPIC_CLIENT_ID \
  --secret EPIC_KID,target=EPIC_KID \
  --secret EPIC_PRIVATE_KEY,target=EPIC_PRIVATE_KEY \
  nw-smartonfhir-epic:latest

# Cerner FHIR
thv run --name nw-smartonfhir-cerner --transport stdio \
  --secret CERNER_FHIR_BASE_URL,target=CERNER_FHIR_BASE_URL \
  --secret CERNER_TOKEN_URL,target=CERNER_TOKEN_URL \
  --secret CERNER_CLIENT_ID,target=CERNER_CLIENT_ID \
  --secret CERNER_KID,target=CERNER_KID \
  --secret CERNER_PRIVATE_KEY,target=CERNER_PRIVATE_KEY \
  --secret CERNER_SCOPES,target=CERNER_SCOPES \
  nw-smartonfhir-cerner:latest

# SMTP
thv run --name nw-smtp --transport stdio \
  --secret SMTP_HOST,target=SMTP_HOST \
  --secret SMTP_PORT,target=SMTP_PORT \
  --secret SMTP_USE_TLS,target=SMTP_USE_TLS \
  --secret SMTP_USERNAME,target=SMTP_USERNAME \
  --secret SMTP_PASSWORD,target=SMTP_PASSWORD \
  --secret FROM_EMAIL,target=FROM_EMAIL \
  nw-smtp:latest

# Stripe
thv run --name nw-stripe --transport stdio \
  --secret STRIPE_API_KEY,target=STRIPE_API_KEY \
  nw-stripe:latest

# Salesforce
thv run --name nw-salesforce --transport stdio \
  --secret SALESFORCE_INSTANCE_URL,target=SALESFORCE_INSTANCE_URL \
  --secret SALESFORCE_TOKEN_URL,target=SALESFORCE_TOKEN_URL \
  --secret SALESFORCE_CLIENT_ID,target=SALESFORCE_CLIENT_ID \
  --secret SALESFORCE_CLIENT_SECRET,target=SALESFORCE_CLIENT_SECRET \
  --secret SALESFORCE_REFRESH_TOKEN,target=SALESFORCE_REFRESH_TOKEN \
  nw-salesforce:latest

# Slack
thv run --name nw-slack --transport stdio \
  --secret SLACK_BOT_TOKEN,target=SLACK_BOT_TOKEN \
  nw-slack:latest
```

> **Google Drive + ToolHive:** Set `GOOGLE_DRIVE_SA_JSON` to the JSON *contents* (not a file path) when storing in ToolHive secrets, because ToolHive injects secrets as string values.

After registration, copy the proxy URLs from the ToolHive UI and set them in your `.env`:

```env
TOOLHIVE_MCP_URLS=http://localhost:7977/mcp,http://localhost:7978/mcp,http://localhost:7979/mcp
```

---

## Agentic workflow integration

The Agentic Workflow (`agents.toolhive`) discovers and merges tools from all registered MCP servers automatically.

### Multi-server (per-connector images — preferred)

```bash
export TOOLHIVE_MCP_URLS="http://localhost:7977/mcp,http://localhost:7978/mcp,http://localhost:7979/mcp"
python -m agents.toolhive --patient-id 12724066 --recipient-email you@example.com
```

### Single server (backward compatible)

```bash
export TOOLHIVE_MCP_URL="http://localhost:34567/mcp"
python -m agents.toolhive --patient-id 12724066 --recipient-email you@example.com
```

### LLM provider selection

Switch the AI backend by setting `LLM_PROVIDER` before running:

```bash
LLM_PROVIDER=openai python -m agents.toolhive --patient-id 12724066 --recipient-email you@example.com
LLM_PROVIDER=anthropic python -m agents.toolhive --patient-id 12724066 --recipient-email you@example.com
```

---

## Local testing (no ToolHive required)

Run each MCP server directly over stdio for quick iteration:

```bash
python -m agents.google_drive_mcp
python -m agents.fhir_epic_mcp
python -m agents.fhir_cerner_mcp
python -m agents.smtp_mcp
```

Inspect any server's tool list interactively with MCP Inspector:

```bash
npx @modelcontextprotocol/inspector python -m agents.fhir_epic_mcp
npx @modelcontextprotocol/inspector python -m agents.fhir_cerner_mcp
npx @modelcontextprotocol/inspector python -m agents.google_drive_mcp
npx @modelcontextprotocol/inspector python -m agents.smtp_mcp
```

Run the full agent locally (bypasses ToolHive, uses stdio directly):

```bash
python -m agents.toolhive --local --patient-id 12724066 --recipient-email you@example.com
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `TOOLHIVE_MCP_URL(S) is not set` | Missing env var | Set `TOOLHIVE_MCP_URL` (single) or `TOOLHIVE_MCP_URLS` (multi) in `.env` |
| `Failed to list MCP tools: Connection refused` | ToolHive proxy stopped | Re-register with `thv run`; confirm the proxy URL matches what ToolHive UI shows |
| Google Drive auth failures | Secret injected as a file path | For ToolHive, set `GOOGLE_DRIVE_SA_JSON` to JSON *contents* (not a path) |
| `fhir_epic connector not configured` | Missing Epic env vars | Ensure all `EPIC_*` variables are set and non-empty |
| `fhir_cerner connector not configured` | Missing Cerner env vars | Ensure all `CERNER_*` variables are set and non-empty |
| Docker build fails with `COPY src/ not found` | Wrong build context | Always run `docker build` from the **repository root**, not from `docker/<name>/` |
| Docker build fails with `COPY packages/connectors/.../dist/*.whl` | Missing wheel | Run `bash scripts/build-packages.sh packages/connectors/<name>` to build the wheel first (Tier 2 must exist before Docker image) |
| Image healthcheck fails | Import error at startup | Run `docker logs <container>` to see the Python traceback; usually a missing env var |
| Log shows many `Connector enabled in configuration but not registered; skipping instantiation` warnings | Expected behaviour | Per-connector images set `NW_ALLOWED_CONNECTORS` to one ID; all other connectors in `config/connectors.yaml` produce this warning and are correctly skipped. Not an error. |
| `MCP server initialized \| manifest_contract=5` — what does the number mean? | Version indicator | `manifest_contract` is the MCP input-schema contract version (currently 5), **not** the number of tools. Use `tools/list` to count exposed tools. |

## Rollout verification checklist

Use this checklist when promoting streamable-http MCP to production:

1. Confirm edge auth gate behavior:
   - No token -> `401 MCP_AUTH_REQUIRED`
   - Invalid token -> `403 MCP_AUTH_INVALID`
   - Valid token -> request reaches MCP handlers
2. Confirm scope baseline:
   - `NW_MCP_SCOPE_POLICY_DEFAULT=deny` is set in deployed env
   - Optionally enforce `NW_MCP_SCOPE_POLICY_STRICT=true`
3. Confirm authorization telemetry:
   - Track trends for 401/403 and `POLICY_DENIED` responses after rollout
   - Verify expected tool visibility changes in `tools/list` for scoped identities
4. Confirm privileged-key controls:
   - Any API key with wildcard scope (`*`) is documented and approved
   - Non-admin API keys use minimal scopes only
