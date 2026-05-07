# Node Wire — Setup Guide

Node Wire is a Python framework that runs connector adapters (Google Drive, SMTP, FHIR, Stripe, and more) and exposes them over REST, gRPC, or MCP. It includes a built-in AI agent layer so LLMs can discover and orchestrate these connectors automatically.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Platform](#running-the-platform)
- [Connectors Overview](#connectors-overview)
- [Connector Setup](#connector-setup)
- [MCP Server & ToolHive](#mcp-server--toolhive)
- [Running Tests](#running-tests)
- [Playground UI](#playground-ui)

---

## Prerequisites


| Requirement | Version | Notes                                   |
| ----------- | ------- | --------------------------------------- |
| Python      | 3.11+   | `python --version` to check             |
| pip or uv   | Latest  | `pip install --upgrade pip`             |
| Git         | Any     | To clone the repo                       |
| Docker      | Latest  | Only needed for ToolHive MCP deployment |
| Node.js     | Any LTS | Only needed for `npx @modelcontextprotocol/inspector` |


---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd <repository-directory>   # the folder git creates (rename if you like)

# 2. Install dependencies (recommended: uv)
uv sync --extra agents

# 3. Verify the install
python -m uv run node-wire --help
```

> **Install uv:** See the official installer docs at `https://docs.astral.sh/uv/`.
>
> **REST/gRPC only** (no AI agent features): `uv sync` without the extra is sufficient.
>
> **Alternative (pip):** If you’re not using `uv`, install editable deps with pip:
>
> - `pip install -e ".[agents]"` (includes MCP/LLM agent dependencies)
> - `pip install -e .` (REST/gRPC only, no agent dependencies)

> **Installing from PyPI wheels instead of source?** See [docs/packaging.md](docs/packaging.md) for the wheel build lifecycle, client install model, and pre-publish validation checklist.

---

## Configuration

All secrets and settings are loaded from environment variables. A template is provided at `sample.env`.

```bash
# Copy the template
cp sample.env .env

# Open and fill in the values you need
```

You only need to fill in the sections for the connectors you plan to use. The platform starts successfully even if some credentials are missing — those connectors will simply return an error when called.

> [!IMPORTANT]
> **Connector Allowlist:** For security, Node Wire uses a fail-closed allowlist for connector entry points. You **must** set `NW_ALLOWED_CONNECTORS` in your `.env` file to a comma-separated list of the connectors you want to enable (e.g., `fhir_epic,http_generic`). If this variable is missing or empty, no connectors will be loaded even if they are enabled in `connectors.yaml`.


> **Doc convention:** Environment variable names in the docs follow `sample.env`. Some legacy keys (like `stripe_api_key`) are intentionally lower-case because that is what the connector reads.

### Environment Variable Sections


| Section          | Key Variables                                                                                                       | When Needed            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| **FHIR Epic**    | `EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_KID`, `EPIC_PRIVATE_KEY`                            | Epic EHR integration   |
| **FHIR Cerner**  | `CERNER_FHIR_BASE_URL`, `CERNER_TOKEN_URL`, `CERNER_CLIENT_ID`, `CERNER_KID`, `CERNER_PRIVATE_KEY`, `CERNER_SCOPES` | Cerner EHR integration |
| **Google Drive** | `GOOGLE_DRIVE_SA_JSON`, `GOOGLE_DRIVE_FOLDER_ID`                                                                    | Google Drive connector |
| **SMTP**         | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`                                                          | Sending emails         |
| **Slack**        | `SLACK_BOT_TOKEN`                                                                                                   | Sending Slack messages |
| **LLM / Agent**  | `LLM_PROVIDER`, `GROQ_API_KEY` (or other provider key)                                                              | AI agent / ToolHive    |
| **ToolHive / MCP**| `TOOLHIVE_MCP_URLS` (multi-server), `NW_MCP_TRANSPORT`, `NW_MCP_PORT`, `NW_STREAM_BUFFER_MS`               | AI agent / ToolHive    |
| **ToolHive**     | `TOOLHIVE_MCP_URL` (single) or `TOOLHIVE_MCP_URLS` (comma-separated, multi-server)                                  | ToolHive MCP proxy     |
| **Plugin Allowlist** | `NW_ALLOWED_CONNECTORS` (comma-separated list of allowed connector names)                                         | **Required** for any connector to load |



See `sample.env` for the full list with example values.

---

## Running the Platform

The platform supports three modes. Set the `MODE` environment variable to switch between them.


| Mode                   | Command                           | Default Port | Use Case                            |
| ---------------------- | --------------------------------- | ------------ | ----------------------------------- |
| **REST API** (default) | `python -m uv run node-wire`                | `8000`       | HTTP clients, Swagger UI, curl      |
| **gRPC**               | `MODE=GRPC python -m uv run node-wire`      | `50051`      | gRPC clients                        |
| **MCP**                | `python -m agents.mcp_entrypoint` | stdio / 8080 | AI agents, ToolHive, Claude Desktop |

> **Important:** `MODE=MCP` for `node-wire` / `python -m bindings_entrypoint` starts a minimal MCP-style placeholder server, not the full stdio MCP server used with ToolHive and the agent layer. For ToolHive/Inspector/agents, use `python -m agents.mcp_entrypoint` (or the per-connector MCP servers in `docs/mcp-servers.md`).

### Configuration file (`config/connectors.yaml`)

Connectors are loaded from `config/connectors.yaml`. Each connector has:

- `enabled`: whether the connector is instantiated at startup
- `exposed_via`: which protocols can access it (`rest`, `grpc`, `mcp`)

If a connector is disabled (or not exposed for a protocol), requests to it will fail with “not configured / not available” even if your `.env` is correct.

For details on adding a new connector to the runtime, see [docs/connectors.md](docs/connectors.md).


### REST API Quick Start

```bash
# Local development: disable REST auth (do not use in production)
export NW_REST_AUTH_DISABLED=true

# Default port 8000
python -m uv run node-wire

# If port 8000 is in use, override with PORT
PORT=8001 python -m uv run node-wire
```

**Production / secured REST:** set `NW_REST_API_KEY` and send `Authorization: Bearer <key>` or `X-API-Key: <key>` on every route except `GET /health`. Set `NW_REST_LOAD_DOTENV=false` so secrets are not loaded from a `.env` file. See [docs/connectors.md](docs/connectors.md) (Security section).

Once running:

- **Health check (no auth):** `GET http://localhost:8000/health`
- **Interactive docs (Swagger UI):** `http://localhost:8000/docs` (requires API key when auth is enabled)
- **Call a connector:** `POST http://localhost:8000/connectors/{connector_id}/{action}`

Example — send an HTTP request via the generic connector (with auth enabled):

```bash
curl -X POST http://localhost:8000/connectors/http_generic/request \
  -H "Authorization: Bearer $NW_REST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://httpbin.org/get", "method": "GET"}'
```

All responses use the same standard shape:

```json
{
  "success": true,
  "data": { "raw": { ... }, "description": "..." },
  "error_code": null,
  "error_category": null,
  "message": null,
  "trace_id": "..."
}
```

---

## Connectors Overview

**Developer guide (`BaseConnector`, config, factory):** [docs/connectors.md](docs/connectors.md).



| Connector        | What It Does                               | Credentials Needed                     | Setup Guide                                                                                   |
| ---------------- | ------------------------------------------ | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| **http_generic** | Make HTTP requests to any URL              | None                                   | No setup needed                                                                               |
| **smtp**         | Send emails via SMTP                       | SMTP host/port/username/password       | [SMTP Setup](#smtp)                                                                           |
| **stripe**       | Process Stripe payments                    | Stripe API key                         | [Stripe Setup](#stripe)                                                                       |
| **google_drive** | List, upload, download, manage Drive files | GCP service account JSON               | [Google Drive setup & API](docs/google_drive_connector.md#google-drive-service-account-setup) |
| **fhir_epic**    | Read/write patient data from Epic EHR      | Epic SMART credentials + private key   | [FHIR Epic Setup](#fhir-epic)                                                                 |
| **fhir_cerner**  | Read/write patient data from Cerner EHR    | Cerner SMART credentials + private key | [FHIR Cerner Setup](#fhir-cerner)                                                             |
| **slack**        | Send messages and files to Slack channels  | Slack Bot OAuth Token                  | [Slack Setup](#slack)                                                                         |


---

## Connector Setup

### HTTP Generic

No credentials required. Works out of the box.

```bash
curl -X POST http://localhost:8000/connectors/http_generic/request \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.example.com/data",
    "method": "POST",
    "headers": {"Authorization": "Bearer your-token"},
    "body": {"key": "value"}
  }'
```

---

### SMTP

Add these to your `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your-app-password
```

> **Gmail users:** You must use an [App Password](https://support.google.com/accounts/answer/185833), not your regular Gmail password. Enable 2-Factor Authentication on your Google account first, then generate an App Password under Security settings.

Supported configurations:

- Port `587` with STARTTLS (recommended for Gmail, most SMTP providers)
- Port `465` with implicit TLS

---

### Stripe

Add to your `.env`:

```env
STRIPE_API_KEY=sk_test_your_key_here
```

Use a **test key** (`sk_test_...`) during development. Switch to a live key (`sk_live_...`) for production.

---

### Google Drive

The Google Drive connector uses a **service account** — a non-human Google account your application uses to authenticate with Google Drive APIs.

**Full documentation:** [docs/google_drive_connector.md](docs/google_drive_connector.md) — service account setup, verification, and REST `execute` API (all seven operations).

Quick summary of what you'll need:

1. A Google Cloud project with the Drive API enabled
2. A service account with a downloaded JSON key file
3. A shared Drive folder (share it with the service account's email)

Add to your `.env`:

```env
GOOGLE_DRIVE_SA_JSON=/absolute/path/to/service-account.json
GOOGLE_DRIVE_FOLDER_ID=your-folder-id-from-drive-url
```

---

### FHIR Epic

Epic EHR integration uses the SMART Backend Services OAuth2 flow with RS384 JWT authentication.

Add to your `.env`:

```env
EPIC_FHIR_BASE_URL=https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
EPIC_TOKEN_URL=https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token
EPIC_CLIENT_ID=your-epic-client-id
EPIC_KID=your-key-id
EPIC_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

You obtain these credentials by registering a backend application in the [Epic App Orchard](https://appmarket.epic.com/) (or your organization's Epic sandbox).

**Available actions:** `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference`

---

### FHIR Cerner

Cerner EHR integration also uses SMART Backend Services with `private_key_jwt` client authentication.

Add to your `.env`:

```env
CERNER_FHIR_BASE_URL=https://fhir-ehr-code.cerner.com/r4/your-tenant-id
CERNER_TOKEN_URL=https://authorization.cerner.com/tenants/your-tenant-id/protocols/oauth2/profiles/smart-v1/token
CERNER_CLIENT_ID=your-cerner-client-id
CERNER_KID=your-key-id
CERNER_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
CERNER_SCOPES="system/Patient.read system/Encounter.read system/DocumentReference.read system/DocumentReference.write"
```

Register your application in the [Cerner Developer Portal](https://code.cerner.com/) to obtain these credentials.

**Available actions:** `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference`

---

## MCP Transport Modes

Node Wire supports two transport modes for AI agents. Switch between them using the `NW_MCP_TRANSPORT` environment variable:

- **`stdio`** (Default): Communicates via standard I/O. Best for local development, subprocess-based clients, and ToolHive-style wrapping. The playground uses buffered agent responses in this mode.
- **`streamable-http`**: Native HTTP MCP server. Exposes a direct endpoint on `NW_MCP_HOST`, `NW_MCP_PORT`, and `NW_MCP_PATH`. The playground streams tool progress and final answer chunks in this mode.

### Streaming Features
- **Configurable Buffering (`NW_STREAM_BUFFER_MS`)**: When streaming, output can be buffered to reduce event spam. Set to the duration in milliseconds (e.g. `2000` for a 2-second batching window). Default is `0` (no buffering).
- **Completion Signals**: The core runtime emits structured "done" signals (`stream_completion_log`) via Python logging when streaming ends, allowing package consumers to easily detect when a stream finishes.

**Example: stdio mode**

```powershell
$env:NW_MCP_TRANSPORT="stdio"
python -m agents.mcp_entrypoint
```

**Example: Shift to HTTP mode on Port 8081**
```powershell
# Windows
$env:NW_MCP_TRANSPORT="streamable-http"
$env:NW_MCP_HOST="127.0.0.1"
$env:NW_MCP_PORT="8081"
$env:NW_MCP_PATH="/mcp"
python -m agents.mcp_entrypoint
```

The HTTP MCP endpoint is then:

```text
http://127.0.0.1:8081/mcp
```

When the REST playground is running, the Agentic Workflow panel displays the active transport by reading `/scenarios/agent-transport`:

- `Transport: stdio`: the UI waits for the complete backend agent result.
- `Transport: Streamable HTTP`: tool cards appear as tools finish, and the final answer renders progressively as chunks arrive.

### Testing with MCP Inspector

MCP Inspector can be launched with `npx`:

```powershell
npx @modelcontextprotocol/inspector
```

For stdio testing, let Inspector launch the server:

```powershell
$env:NW_MCP_TRANSPORT="stdio"
npx @modelcontextprotocol/inspector python -m agents.mcp_entrypoint
```

For streamable HTTP testing, start the server first:

```powershell
$env:NW_MCP_TRANSPORT="streamable-http"
$env:NW_MCP_HOST="127.0.0.1"
$env:NW_MCP_PORT="8081"
$env:NW_MCP_PATH="/mcp"
python -m agents.mcp_entrypoint
```

Then run Inspector:

```powershell
npx @modelcontextprotocol/inspector
```

In Inspector, choose `Streamable HTTP`, enter `http://127.0.0.1:8081/mcp`, connect, then use `Tools -> List Tools` and run a safe tool call with valid JSON arguments.

---
### Slack

Add the bot token to your `.env`:

```env
SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
```

1. Create a Slack App at [api.slack.com/apps](https://api.slack.com/apps).
2. Go to **OAuth & Permissions** and add **Bot Token Scopes**:
   - `chat:write` (to post messages)
   - `files:write` (to upload files)
   - `im:write` (to send direct messages)
   - `conversations:open` (to resolve User IDs)
3. Install the app to your workspace.
4. Copy the **Bot User OAuth Token** (`xoxb-...`).
5. Invite the bot to any private channels you want it to access.

---

## MCP Server & ToolHive

The platform exposes connector tools for AI agents via the MCP (Model Context Protocol). There are two deployment modes:

### Individual MCP servers (recommended)

Each connector runs as its own independent MCP server. This is the preferred approach for modular, scalable deployments.


| Image                   | MCP tools (manifest) | Docker image                     |
| ----------------------- | -------------------- | -------------------------------- |
| `nw-google-drive`       | All `google_drive.<action>` (e.g. `google_drive.files.upload`) | `docker/google-drive/Dockerfile` |
| `nw-smartonfhir-epic`   | All `fhir_epic.<action>` (e.g. `fhir_epic.read_patient`) | `docker/fhir-epic/Dockerfile`    |
| `nw-smartonfhir-cerner` | All `fhir_cerner.<action>` (e.g. `fhir_cerner.read_patient`) | `docker/fhir-cerner/Dockerfile`  |
| `nw-smtp`               | `smtp.send_email`    | `docker/smtp/Dockerfile`         |
| `nw-slack`              | All `slack.<action>` (e.g. `slack.post_message`)     | `docker/slack/Dockerfile`        |


**Full guide (build, env config, ToolHive registration, multi-server agent usage):** [docs/mcp-servers.md](docs/mcp-servers.md)

**FHIR tool arguments (Cerner / Epic)** — tool names are `fhir_cerner.<action>` and `fhir_epic.<action>`. Use field names from `tools/list` / the connector manifest. Typical payloads:

| Action | When to use | Example arguments |
| ------ | ----------- | ------------------- |
| `read_patient` | You have a Patient id | `{"resource_id": "12724066"}` (Epic ids often start with `e`) |
| `search_patients` | No id, or name-based search | `{"resource_ids": ["id1"]}` or `{"given_name": "...", "family_name": "..."}` or `{"search_params": {"identifier": "...", "family": "..."}}` (FHIR search param names) |

The MCP server normalizes common LLM/legacy aliases (`patientId` / `patient_id` → `resource_id`; `patientId` inside `search_params` → `identifier`) before validation. Prefer canonical fields above when authoring prompts or clients.

Quick start:

```bash
# Build all four images
./scripts/build-mcp-images.sh

# Start all four locally
docker compose -f docker-compose.mcp.yml up
```

### Combined MCP server (all connectors in one)

For simpler setups all connectors can be exposed from a single MCP server:

```bash
python -m agents.mcp_entrypoint
```

**ToolHive** runs the MCP server inside a secure Docker container, manages secrets injection, and provides an HTTP proxy that any MCP-compatible client (Claude Desktop, Cursor, custom agents) can connect to.

**See the full ToolHive workflow guide:** [docs/toolhive_agent_scenario.md](docs/toolhive_agent_scenario.md)

### Quick Local Test (No ToolHive)

```bash
# Inspect any individual server with MCP Inspector
npx @modelcontextprotocol/inspector python -m agents.fhir_epic_mcp
npx @modelcontextprotocol/inspector python -m agents.google_drive_mcp

# Or test the combined server
npx @modelcontextprotocol/inspector python -m agents.mcp_entrypoint
```

For native streamable HTTP testing, use the `streamable-http` setup above and connect Inspector to `http://127.0.0.1:8081/mcp`.

### Troubleshooting quick hits

- **Port 8000 in use**: set `PORT=8001` (or any free port) when starting the REST API.
- **Connector “not configured”**: confirm it is `enabled: true` (and exposed for your protocol) in `config/connectors.yaml`.
- **ToolHive + Google Drive auth failure**: inside ToolHive, `GOOGLE_DRIVE_SA_JSON` must be the JSON **contents** (not a file path). Locally, it can be an absolute file path (see `docs/mcp-servers.md`).

---

## Running Tests

```bash
# Install dev dependencies (if not already installed)
pip install -e ".[dev,agents]"

# Run all tests
pytest tests/ -v

# Run a specific connector's tests
pytest tests/test_google_drive.py -v
pytest tests/test_fhir_epic.py -v
pytest tests/test_toolhive_agent.py -v
```

Most tests are unit tests that run without real credentials. Integration tests that call live APIs are skipped unless the relevant environment variables are set.

---

## Playground UI

The repository includes an interactive web playground that showcases 5 orchestration scenarios:

> **Note:** The UI is served under the `/playground/` path (not at the server root).

```bash
# Start the REST API (if not already running)
python -m uv run node-wire

# Open in your browser
open http://localhost:8000/playground/
```

Scenarios include:

1. Epic FHIR patient lookup and clinical note upload
2. IT Ops automation via HTTP Generic
3. Cerner FHIR orchestration
4. Google Drive document archival
5. AI agent orchestration via MCP

See `playground/README.md` for details on each scenario and how to configure them.
