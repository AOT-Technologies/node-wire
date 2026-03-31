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
| Python      | 3.12+   | `python --version` to check             |
| pip or uv   | Latest  | `pip install --upgrade pip`             |
| Git         | Any     | To clone the repo                       |
| Docker      | Latest  | Only needed for ToolHive MCP deployment |


---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd connector-platform

# 2. Install dependencies (recommended: uv)
uv sync --extra agents

# 3. Verify the install
uv run node-wire --help
```

> **REST/gRPC only** (no AI agent features): `uv sync` without the extra is sufficient.
>
> **Alternative (pip):** If you’re not using `uv`, install editable deps with pip:
>
> - `pip install -e ".[agents]"` (includes MCP/LLM agent dependencies)
> - `pip install -e .` (REST/gRPC only, no agent dependencies)

---

## Configuration

All secrets and settings are loaded from environment variables. A template is provided at `sample.env`.

```bash
# Copy the template
cp sample.env .env

# Open and fill in the values you need
```

You only need to fill in the sections for the connectors you plan to use. The platform starts successfully even if some credentials are missing — those connectors will simply return an error when called.

### Environment Variable Sections


| Section          | Key Variables                                                                                                       | When Needed            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| **FHIR Epic**    | `EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_KID`, `EPIC_PRIVATE_KEY`                            | Epic EHR integration   |
| **FHIR Cerner**  | `CERNER_FHIR_BASE_URL`, `CERNER_TOKEN_URL`, `CERNER_CLIENT_ID`, `CERNER_KID`, `CERNER_PRIVATE_KEY`, `CERNER_SCOPES` | Cerner EHR integration |
| **Google Drive** | `google_drive_sa_json`, `GOOGLE_DRIVE_FOLDER_ID`                                                                    | Google Drive connector |
| **SMTP**         | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`                                                          | Sending emails         |
| **LLM / Agent**  | `LLM_PROVIDER`, `GROQ_API_KEY` (or other provider key)                                                              | AI agent / ToolHive    |
| **ToolHive**     | `TOOLHIVE_MCP_URL` (single) or `TOOLHIVE_MCP_URLS` (comma-separated, multi-server)                                  | ToolHive MCP proxy     |


See `sample.env` for the full list with example values.

---

## Running the Platform

The platform supports three modes. Set the `MODE` environment variable to switch between them.


| Mode                   | Command                           | Default Port | Use Case                            |
| ---------------------- | --------------------------------- | ------------ | ----------------------------------- |
| **REST API** (default) | `uv run node-wire`                | `8000`       | HTTP clients, Swagger UI, curl      |
| **gRPC**               | `MODE=GRPC uv run node-wire`      | `50051`      | gRPC clients                        |
| **MCP (stdio)**        | `python -m agents.mcp_entrypoint` | stdio        | AI agents, ToolHive, Claude Desktop |


### REST API Quick Start

```bash
# Default port 8000
uv run node-wire

# If port 8000 is in use, override with PORT
PORT=8001 uv run node-wire
```

Once running:

- **Health check:** `GET http://localhost:8000/health`
- **Interactive docs (Swagger UI):** `http://localhost:8000/docs`
- **Call a connector:** `POST http://localhost:8000/connectors/{connector_id}/{action}`

Example — send an HTTP request via the generic connector:

```bash
curl -X POST http://localhost:8000/connectors/http_generic/request \
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


| Connector        | What It Does                               | Credentials Needed                     | Setup Guide                                                                                   |
| ---------------- | ------------------------------------------ | -------------------------------------- | --------------------------------------------------------------------------------------------- |
| **http_generic** | Make HTTP requests to any URL              | None                                   | No setup needed                                                                               |
| **smtp**         | Send emails via SMTP                       | SMTP host/port/username/password       | [SMTP Setup](#smtp)                                                                           |
| **stripe**       | Process Stripe payments                    | Stripe API key                         | [Stripe Setup](#stripe)                                                                       |
| **google_drive** | List, upload, download, manage Drive files | GCP service account JSON               | [Google Drive setup & API](docs/google_drive_connector.md#google-drive-service-account-setup) |
| **fhir_epic**    | Read/write patient data from Epic EHR      | Epic SMART credentials + private key   | [FHIR Epic Setup](#fhir-epic)                                                                 |
| **fhir_cerner**  | Read/write patient data from Cerner EHR    | Cerner SMART credentials + private key | [FHIR Cerner Setup](#fhir-cerner)                                                             |


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
stripe_api_key=sk_test_your_key_here
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
google_drive_sa_json=/absolute/path/to/service-account.json
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

**Available actions:** `read_patient`, `search_encounter`, `create_document_reference`, `search_document_reference`

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

**Available actions:** `read_patient`, `search_encounter`, `create_document_reference`, `search_document_reference`

---

## MCP Server & ToolHive

The platform exposes connector tools for AI agents via the MCP (Model Context Protocol). There are two deployment modes:

### Individual MCP servers (recommended)

Each connector runs as its own independent MCP server. This is the preferred approach for modular, scalable deployments.


| Image                   | Tool exposed               | Docker image                     |
| ----------------------- | -------------------------- | -------------------------------- |
| `nw-google-drive`       | `google_drive_upload_file` | `docker/google-drive/Dockerfile` |
| `nw-smartonfhir-epic`   | `fhir_epic_read_patient`   | `docker/fhir-epic/Dockerfile`    |
| `nw-smartonfhir-cerner` | `fhir_cerner_read_patient` | `docker/fhir-cerner/Dockerfile`  |
| `nw-smtp`               | `smtp_send_email`          | `docker/smtp/Dockerfile`         |


**Full guide (build, env config, ToolHive registration, multi-server agent usage):** [docs/mcp-servers.md](docs/mcp-servers.md)

Quick start:

```bash
# Build all three images
./scripts/build-mcp-images.sh

# Start all three locally
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
uv run node-wire

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