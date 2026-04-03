# Node Wire — Individual MCP Servers

This document covers everything needed to build, run, configure, and integrate the per-connector MCP servers with ToolHive and the Agentic Workflow.

---

## Table of contents

- [Architecture](#architecture)
- [Naming conventions](#naming-conventions)
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

```mermaid
flowchart TD
    Agent[AgenticWorkflow]
    subgraph toolhive[ToolHive]
        GDrive[nw-google-drive]
        Epic[nw-smartonfhir-epic]
        Cerner[nw-smartonfhir-cerner]
        SMTP[nw-smtp]
    end
    Agent -->|"TOOLHIVE_MCP_URLS"| GDrive
    Agent -->|"TOOLHIVE_MCP_URLS"| Epic
    Agent -->|"TOOLHIVE_MCP_URLS"| Cerner
    Agent -->|"TOOLHIVE_MCP_URLS"| SMTP
```

---

## Naming conventions

| Connector | Python entrypoint | Docker image | ToolHive name | MCP tools exposed |
|---|---|---|---|---|
| Google Drive | `python -m agents.google_drive_mcp` | `nw-google-drive` | `nw-google-drive` | All manifest actions for `google_drive` (names `google_drive.<action>`, e.g. `google_drive.files.upload`) |
| SMART on FHIR (Epic) | `python -m agents.fhir_epic_mcp` | `nw-smartonfhir-epic` | `nw-smartonfhir-epic` | All manifest actions for `fhir_epic` (e.g. `fhir_epic.read_patient`) |
| SMART on FHIR (Cerner) | `python -m agents.fhir_cerner_mcp` | `nw-smartonfhir-cerner` | `nw-smartonfhir-cerner` | All manifest actions for `fhir_cerner` (e.g. `fhir_cerner.read_patient`) |
| SMTP | `python -m agents.smtp_mcp` | `nw-smtp` | `nw-smtp` | `smtp.send_email` |

The unified server (`python -m agents.mcp_entrypoint`) exposes **every** connector enabled for MCP in `config/connectors.yaml` (e.g. `http_generic.request`, `stripe.charge`, plus the rows above).

### Tool arguments and security

- Tool name (`<connector_id>.<action>`) determines the routed action; do not rely on a separate `action` field in the JSON body to select a different operation.
- Per-action normalizers in `src/node_wire_runtime/mcp_normalizers.py` map common LLM mistakes to canonical schema fields; see also `src/node_wire_runtime/ingress.py` for shared MCP/REST behavior.
- **`tools/list` input schemas** omit the `action` field (manifest contract v2+). Pass only the fields shown in `inputSchema`; the server injects `action` from the tool name.

**Legacy rollout (Google Drive `google_drive.files.upload` only):**

| Variable | Values | Purpose |
|----------|--------|---------|
| `NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD` | `warn` (default), `allow` (same mapping, no deprecation log), `reject` | Legacy payload `action: "upload"` for `google_drive.files.upload`. Use `reject` once all clients omit `action` or use canonical `files.upload` only in pre-invoke validation paths. |

---

## Environment configuration

Copy `sample.env` to `.env` and fill in the sections for the servers you plan to run. Each server only reads the env vars from its own section — you do not need to configure all connectors.

```bash
cp sample.env .env
```

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

The SMTP MCP server exposes one tool: `smtp.send_email`. When running under ToolHive, inject these as secrets:

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

All four images are built from the repository root using the automation script:

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
```

> **Note:** The build context must be the repository root (`.`) so the `COPY src/` and `COPY config/` instructions resolve correctly.

---

## Run with docker-compose

`docker-compose.mcp.yml` starts all four MCP servers as stdio containers in one command. This is useful for local validation before configuring ToolHive.

```bash
# Ensure your .env is populated, then:
docker compose -f docker-compose.mcp.yml up
```

To start only a specific server:

```bash
docker compose -f docker-compose.mcp.yml up nw-smartonfhir-epic
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
| Image healthcheck fails | Import error at startup | Run `docker logs <container>` to see the Python traceback; usually a missing env var |
