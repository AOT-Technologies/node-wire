<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# ToolHive Agent Scenario: FHIR → Google Drive → Email

> **End-to-end guide for running Node Wire as an MCP server on ToolHive, and connecting an AI agent to orchestrate healthcare and enterprise workflows.**

This guide walks you through running the platform as an MCP server using ToolHive and driving the **FHIR → Google Drive → email** workflow with the bundled agent (`agents.toolhive`).

**What you'll build:** An AI agent that can autonomously fetch patient data from an EHR (Epic or Cerner), upload a summary to Google Drive, and send a notification email — all driven by a single natural language prompt.

**Time to complete:** ~20 minutes (plus credential procurement time)

---

## Table of contents

- [Architecture](#architecture)
- [What is ToolHive?](#what-is-toolhive)
- [What does the Node Wire MCP server expose?](#what-does-the-node-wire-mcp-server-expose)
- [Prerequisites](#prerequisites)
- [Step 1: Build the Docker image](#step-1-build-the-docker-image)
- [Step 2: Prepare your secrets](#step-2-prepare-your-secrets)
- [Step 3: Store secrets in ToolHive](#step-3-store-secrets-in-toolhive)
- [Step 4: Register the MCP server in ToolHive](#step-4-register-the-mcp-server-in-toolhive)
- [Step 5: Configure the agent](#step-5-configure-the-agent)
- [Step 6: Run the agent](#step-6-run-the-agent)
- [Local testing (no ToolHive required)](#local-testing-no-toolhive-required)
- [Claude Desktop and Cursor integration](#claude-desktop-and-cursor-integration)
- [Troubleshooting](#troubleshooting)
- [Running tests](#running-tests)
- [File layout (`agents`)](#file-layout-agents)
- [Related documentation](#related-documentation)

---

## Architecture

```
ToolHive UI  ──────────────────────────────────────────────────────
│  MCP Server (Docker): node-wire                     │
│  ├── Tool: fhir_cerner.read_patient   ← fetch patient from Cerner │
│  ├── Tool: fhir_epic.read_patient     ← fetch patient from Epic   │
│  ├── Tool: google_drive.files.upload  ← write file to Drive       │
│  ├── Tool: stripe.charge              ← process payment           │
│  └── Tool: smtp.send_email            ← email the summary         │
│                         ↕ stdio → HTTP proxy                      │
──────────────────────────────────────────────────────────────────
         ↕ MCP JSON-RPC over HTTP
 ┌───────────────────────────┐
 │  Agent Script (local)     │
 │  toolhive.py              │
 │  LLM: Groq / OpenAI /     │
 │       Gemini / Claude      │
 └───────────────────────────┘
```

ToolHive runs the connector platform in a secure Docker container, injects secrets as environment variables, and exposes an HTTP proxy. The agent script connects to that proxy, discovers tools via `tools/list`, and orchestrates the workflow using an LLM's tool-calling capability.

---

## Individual connector MCP servers

For modular deployments, each connector can be run as an independent MCP server container:

- `nw-google-drive` (Google Drive)
- `nw-smartonfhir-epic` (Epic SMART on FHIR)
- `nw-smartonfhir-cerner` (Cerner SMART on FHIR)
- `nw-smtp` (SMTP email)

When running multiple MCP servers, configure the agent with **`TOOLHIVE_MCP_URLS`** (comma-separated list of ToolHive proxy URLs). The agent will merge tools across servers.

**Full guide:** [docs/mcp-servers.md](mcp-servers.md)

---

## What is ToolHive?

[ToolHive](https://stacklok.com/toolhive) is a desktop application that:
- Runs MCP servers inside secure Docker containers
- Injects secrets as environment variables (so they never appear in config files)
- Exposes an HTTP proxy so any MCP-compatible client can connect

You can think of it as a local "MCP server manager" — you register your server once, and ToolHive handles starting it, securing its secrets, and making it available to AI tools.

---

## What does the Node Wire MCP server expose?

When running **this scenario’s** minimal multi-connector stack (one MCP server per connector image registered in ToolHive), agents typically see **four** tools (Cerner read patient, Epic read patient, Drive upload, SMTP send). The **unified** MCP server (`python -m agents.mcp_entrypoint`) exposes **all** manifest actions for every connector enabled for MCP in `config/connectors.yaml` (often 18+ tools). This section describes the **four-tool** happy path; see [mcp-servers.md](mcp-servers.md) for the full surface.

| Tool | Description |
|---|---|
| `fhir_cerner.read_patient` | Fetch a patient's record from a Cerner FHIR R4 endpoint |
| `fhir_epic.read_patient` | Fetch a patient's record from an Epic FHIR R4 endpoint |
| `google_drive.files.upload` | Create and upload a text file to Google Drive |
| `stripe.charge` | Process a payment |
| `smtp.send_email` | Send an email via SMTP |

The agent uses an LLM's tool-calling capability to decide which tools to call, in what order, and with what parameters.

### PHI protection built-in

The MCP server automatically masks sensitive health information before any data leaves the FHIR system:
- Date of birth: year is masked (`****-MM-DD`)
- Patient IDs: partially masked (`123*****`)
- When emailing summaries, the agent uploads data to Drive first and sends a **link** — not raw patient data

---

## Prerequisites

| Requirement | Notes |
|---|---|
| [ToolHive UI](https://stacklok.com/toolhive) installed | Download for macOS / Linux / Windows |
| Docker running | Only needed to build the image |
| Cerner FHIR credentials | `client_id`, `kid`, private key, tenant URL |
| Google Drive service account JSON | `service_account.json` or file path |
| SMTP credentials | Gmail App Password recommended |
| Groq API key (free) | [console.groq.com](https://console.groq.com) |

---

## Environment variables (complete list)

Below is the full set of environment variables used by the connector platform and the agent. Values marked "Required" must be provided for the matching connector to work.

| Variable | Required for | Notes / Example value |
|---|---:|---|
| `CERNER_CLIENT_ID` | Cerner connector | Required for Cerner FHIR OAuth |
| `CERNER_FHIR_BASE_URL` | Cerner connector | Base FHIR URL for Cerner |
| `CERNER_KID` | Cerner connector | Key ID for private key (kid) |
| `CERNER_PRIVATE_KEY` | Cerner connector | PEM private key (paste full block) |
| `CERNER_TOKEN_URL` | Cerner connector | Token endpoint URL |
| `EPIC_CLIENT_ID` | Epic connector | Required for Epic OAuth |
| `EPIC_FHIR_BASE_URL` | Epic connector | Base FHIR URL for Epic |
| `EPIC_KID` | Epic connector | Key ID for private key (kid) |
| `EPIC_PRIVATE_KEY` | Epic connector | PEM private key (paste full block) |
| `EPIC_TOKEN_URL` | Epic connector | e.g. `https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token` |
| `FROM_EMAIL` | Email sending | Example: `from@example.com` |
| `GROQ_API_KEY` | LLM (Groq) | Your Groq API key |
| `GROQ_MODEL` | LLM | Example: `openai/gpt-oss-120b` |
| `NW_MCP_TRANSPORT` | ToolHive / local | `stdio` when running in ToolHive container |
| `PYTHONPATH` | Runtime | e.g. `/app/src` for container; `**/node-wire/src` locally |
| `SMTP_HOST` | SMTP connector | Example: `sandbox.smtp.mailtrap.io` |
| `SMTP_PORT` | SMTP connector | Example: `2525` |
| `SMTP_USERNAME` | SMTP connector | Mailtrap / SMTP user |
| `SMTP_PASSWORD` | SMTP connector | Mailtrap / SMTP password |
| `SMTP_USE_TLS` | SMTP connector | `true` or `false` |
| `GOOGLE_DRIVE_SA_JSON` | Google Drive | Either paste full JSON into ToolHive secret or provide absolute file path to the service account JSON |

---

## Quick start (non-developers)

These two methods let a non-developer get the agent running quickly: the recommended path uses the ToolHive UI (no code), and the local path is for someone who can run a single PowerShell script.

Option A — Recommended: ToolHive UI (no code)

1. Open the ToolHive UI on your machine.
2. Build or pull the Docker image `node-wire:latest` (admins can do this for you), then Add a new Server / Container.
3. Name it `node-wire-connectors`. Set Transport to `stdio`.
4. In the server's Environment / Secrets section, add the variables from the table above. For `GOOGLE_DRIVE_SA_JSON` paste the entire service account JSON into the secret value (do NOT upload a file path here).
5. Start the server. ToolHive will show an Endpoint URL like `http://localhost:<PORT>/sse` or a proxy URL that contains `/sse` or `/mcp`.
6. Copy the proxy URL and paste it into a local `.env` or give it to the person running the agent as `TOOLHIVE_MCP_URL`.

Option B — Local quick run (Windows PowerShell)

Prerequisite: Install Python 3.11+ and Git. If you cannot install, ask an administrator to run Option A.

1. Open PowerShell and clone or navigate to the project folder.
2. Create a simple `.env` file in the project root (replace placeholder values):

```powershell
Set-Content -Path .env -Value @'
TOOLHIVE_MCP_URL=http://localhost:7977/mcp
LLM_PROVIDER=groq
GROQ_API_KEY=YOUR_GROQ_KEY
GROQ_MODEL=openai/gpt-oss-120b
FROM_EMAIL=from@example.com
SMTP_HOST=sandbox.smtp.mailtrap.io
SMTP_PORT=2525
SMTP_USERNAME=your_smtp_user
SMTP_PASSWORD=your_smtp_pass
SMTP_USE_TLS=true
GOOGLE_DRIVE_FOLDER_ID=YOUR_FOLDER_ID
'@
```

3. Install the Python extras (one-time):

```powershell
pip install -e ".[agents]"
```

4. Run the agent (example):

```powershell
#$env:TOOLHIVE_MCP_URL="http://localhost:7977/mcp"
python -m agents.toolhive --patient-id 12724066 --recipient-email you@example.com
```

Notes for non-developers:
- If you see errors about missing credentials, re-open `.env` and ensure each secret is filled in. Use the ToolHive UI option if you can't edit `.env` safely.
- For email testing you can use Mailtrap or Mailhog (no real emails sent).


## Step 1: Build the Docker image

From the root of the repository, build the Python wheels first (required for the Docker image):

```bash
bash scripts/build-packages.sh
```

Then build the container image:

```bash
docker build -t node-wire:latest .
```

This packages the MCP server and all dependencies into a self-contained image. The image's default command is `python -m agents.mcp_entrypoint`.

Verify the build succeeded:

```bash
docker images | grep node-wire
```

You should see `node-wire   latest   <image-id>   ...`

---

## Step 2: Prepare your secrets

ToolHive injects these as environment variables inside the container at runtime. They are never stored in config files or Docker images.

Gather the following values before proceeding:

| Secret Name | Description | Where to Get It |
|---|---|---|
| `CERNER_FHIR_BASE_URL` | Your Cerner FHIR R4 base URL | Cerner developer portal, format: `https://fhir-ehr-code.cerner.com/r4/<tenant-id>` |
| `CERNER_CLIENT_ID` | Cerner app client ID | Cerner developer portal |
| `CERNER_KID` | Key ID for your RSA key pair | You choose this when registering the app |
| `CERNER_PRIVATE_KEY` | RSA private key (full PEM block) | Generated when you registered your app |
| `CERNER_TOKEN_URL` | Cerner OAuth2 token endpoint | Format: `https://authorization.cerner.com/tenants/<tenant-id>/...` |
| `GOOGLE_DRIVE_SA_JSON` | Contents of your service account JSON (not the file path — paste the full JSON string) | See [Google Drive service account setup](google_drive_connector.md#google-drive-service-account-setup) |
| `SMTP_USERNAME` | Full email address (must include `@`) | Your email address, e.g. `you@gmail.com` |
| `SMTP_PASSWORD` | App password for SMTP | For Gmail: [create an App Password](https://support.google.com/accounts/answer/185833) |
| `SMTP_HOST` | SMTP server hostname | `smtp.gmail.com` for Gmail |
| `SMTP_PORT` | SMTP port | `587` for STARTTLS, `465` for implicit TLS |

> **Epic users:** If using Epic instead of Cerner, replace the `CERNER_*` variables with their `EPIC_*` equivalents (`EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_KID`, `EPIC_PRIVATE_KEY`).

> **Note on `GOOGLE_DRIVE_SA_JSON`:** Paste the **entire contents** of the service account JSON file as the secret value — not the file path. This is because the Docker container doesn't have access to your local filesystem.

---

## Step 3: Store secrets in ToolHive

### Option A: ToolHive UI (recommended for beginners)

1. Open the **ToolHive** application.
2. Navigate to **Secrets** (or **Environment Variables**) in the sidebar.
3. For each secret in the table above, click **Add Secret** and enter the name and value.

### Option B: ToolHive CLI (`thv`)

If you have the `thv` CLI installed:

```bash
thv secret set CERNER_FHIR_BASE_URL
# You'll be prompted to enter the value securely

thv secret set CERNER_CLIENT_ID
thv secret set CERNER_KID
thv secret set CERNER_PRIVATE_KEY
thv secret set CERNER_TOKEN_URL
thv secret set GOOGLE_DRIVE_SA_JSON
thv secret set SMTP_USERNAME
thv secret set SMTP_PASSWORD
thv secret set SMTP_HOST
thv secret set SMTP_PORT
```

---

## Step 4: Register the MCP server in ToolHive

### Option A: ToolHive UI

1. In the ToolHive app, go to **Servers** → **Add Server**.
2. Select **Docker Container** or **Custom Server**.
3. Fill in the fields:
   - **Name:** `node-wire-connectors`
   - **Image:** `node-wire:latest`
   - **Transport:** `stdio`
4. Under the **Secrets** or **Environment** tab, link all the secrets you stored in Step 3.
5. Click **Start** or **Deploy**.

ToolHive will start the container and set up a stdio-to-HTTP proxy on a local port.

### Option B: ToolHive CLI

```bash
thv run \
  --name node-wire-connectors \
  --transport stdio \
  --secret CERNER_FHIR_BASE_URL,target=CERNER_FHIR_BASE_URL \
  --secret CERNER_CLIENT_ID,target=CERNER_CLIENT_ID \
  --secret CERNER_KID,target=CERNER_KID \
  --secret CERNER_PRIVATE_KEY,target=CERNER_PRIVATE_KEY \
  --secret CERNER_TOKEN_URL,target=CERNER_TOKEN_URL \
  --secret GOOGLE_DRIVE_SA_JSON,target=GOOGLE_DRIVE_SA_JSON \
  --secret SMTP_USERNAME,target=SMTP_USERNAME \
  --secret SMTP_PASSWORD,target=SMTP_PASSWORD \
  --secret SMTP_HOST,target=SMTP_HOST \
  --secret SMTP_PORT,target=SMTP_PORT \
  node-wire:latest
```

### What you should see

In the ToolHive UI under **Installed**, you should see:

| Field | Value |
|---|---|
| Name | `node-wire-connectors` |
| Status | `Running` |
| Tools | Manifest-driven `<connector_id>.<action>` (e.g. `fhir_cerner.read_patient`, `fhir_epic.read_patient`, `google_drive.files.upload`, `smtp.send_email`; unified server also lists Stripe, HTTP generic, and other MCP-enabled connectors) |
| Endpoint | `http://localhost:<auto-port>/sse` |

---

## Step 5: Configure the agent

Copy the proxy URL from the ToolHive UI (shown in the server's details page) and add it to your local `.env` file:

```env
# Replace PORT with the actual port number shown in ToolHive
TOOLHIVE_MCP_URL=http://localhost:34567/mcp

# LLM provider (groq is recommended — it's fast and has a free tier)
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_your_key_here
```

---

## Step 6: Run the agent

### Install agent dependencies (one-time)

```bash
pip install -e ".[agents]"
```

### Execute the scenario

```bash
python -m agents.toolhive \
  --patient-id 12724066 \
  --recipient-email your-email@example.com \
  --drive-folder-id "1ABCdef_your_folder_id"
```

**Arguments:**

| Argument | Required | Description |
|---|---|---|
| `--patient-id` | Yes (or `--patient-family` + `--patient-given`) | Numeric patient ID in the EHR system |
| `--patient-family` | Alternative to `--patient-id` | Patient last name for name-based search |
| `--patient-given` | Used with `--patient-family` | Patient first name |
| `--recipient-email` | Yes | Where to send the patient summary |
| `--drive-folder-id` | No | Google Drive folder ID; defaults to `GOOGLE_DRIVE_FOLDER_ID` env var |
| `--max-steps` | No | Maximum LLM reasoning steps (default: 10) |

### Switching LLM providers

```bash
# Use OpenAI
LLM_PROVIDER=openai OPENAI_API_KEY=sk-... python -m agents.toolhive ...

# Use Claude (Anthropic)
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... python -m agents.toolhive ...

# Use Google Gemini
LLM_PROVIDER=gemini GEMINI_API_KEY=AIza... python -m agents.toolhive ...
```

### Sample output

```
============================================================
Node Wire ToolHive Agent
Provider : groq
MCP URL  : http://localhost:34567/mcp
============================================================
Task: Patient ID: 12724066
Please:
1. Fetch the patient's details from Cerner FHIR (or Epic if the ID starts with 'e').
2. Create a text file named 'patient_summary_12724066.txt' in Google Drive.
3. Send an email to your-email@example.com with a link to the Drive file.

============================================================
RESULT
============================================================
✅ Success  | trace_id=3e4f5a6b-...

Final Answer:
I have completed all three steps:
1. Fetched patient Nancy Smart (DOB: ****-03-15) from Cerner FHIR.
2. Created 'patient_summary_12724066.txt' in Google Drive (file_id: 1XYZ...).
3. Sent a summary email to your-email@example.com with a link to the file.

Steps executed (3):
  ✓ Step 1: fhir_cerner.read_patient
       result : {"patient_id": "123*****", "full_name": "Nancy Smart", ...}
  ✓ Step 2: google_drive.files.upload
       result : {"file_id": "1XYZ...", "web_view_link": "https://docs.google.com/..."}
  ✓ Step 3: smtp.send_email
       result : {"sent": true}
```

---

## Local testing (no ToolHive required)

You can test the full end-to-end flow on your local machine without ToolHive.

### Option A: stdio mode (`--local` flag)

This launches the MCP server as a subprocess and connects to it directly:

```bash
# Set your credentials in .env first, then:
export GROQ_API_KEY="your-key"

python -m agents.toolhive \
  --local \
  --patient-id 12724066 \
  --recipient-email your-email@example.com
```

> The `--local` flag skips the ToolHive proxy and talks to the MCP server over stdio. This is the easiest way to test without Docker.

### Option B: MCP Inspector

The MCP Inspector is a browser-based tool for inspecting and manually calling MCP tools:

```bash
npx @modelcontextprotocol/inspector python -m agents.mcp_entrypoint
```

Open the URL it prints (usually `http://localhost:5173`) to browse the available tools and send test requests.

### Option C: local email testing with Mailhog

If you don't want to use a real SMTP server while testing:

```bash
# Start a local fake SMTP server
docker run -d -p 1025:1025 -p 8025:8025 mailhog/mailhog
```

Update your `.env` (for a **local** agent, not inside ToolHive's container):

```env
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
```

View captured emails at `http://localhost:8025`.

**ToolHive + Mailhog:** If the MCP server runs in Docker and Mailhog runs on the host, point ToolHive secrets at the host SMTP port (e.g. `SMTP_HOST` = `host.docker.internal`, `SMTP_PORT` = `1025` on macOS/Windows Docker Desktop) and disable TLS if your Mailhog setup expects plain SMTP. Re-register or restart the server after changing secrets.

---

## Claude Desktop and Cursor integration

Once ToolHive is running `node-wire-connectors`, any MCP-compatible client can connect to it.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "node-wire-connectors": {
      "url": "http://localhost:<PORT>/sse"
    }
  }
}
```

Replace `<PORT>` with the port shown in ToolHive UI. The four Node Wire connector tools will appear in Claude's tool sidebar automatically.

### Cursor

In Cursor's MCP settings, add the same endpoint URL. The tools will appear in the agent's tool list.

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---|---|---|
| `TOOLHIVE_MCP_URL is not set` | Missing env var | Copy the endpoint URL from ToolHive UI → Installed → `node-wire-connectors` and add to `.env` |
| `Failed to list MCP tools: Connection refused` | ToolHive server stopped | Re-start via ToolHive UI, or run `thv run ...` again; check `thv list` to see running servers |
| `Secret 'CERNER_PRIVATE_KEY' is not configured` | Secret not stored in ToolHive | Run `thv secret set CERNER_PRIVATE_KEY` or add it via the ToolHive UI |
| `google_drive connector: authentication failed` | `GOOGLE_DRIVE_SA_JSON` is a file path, not JSON content | For ToolHive, paste the actual JSON *contents* of the file (not the file path) as the secret value; for local `.env`, use an absolute path to the JSON file per [Google Drive service account setup](google_drive_connector.md#google-drive-service-account-setup) |
| `SMTP authentication failed` | Wrong username or password | For Gmail, use an App Password not your regular password; confirm `SMTP_USERNAME` includes `@` |
| `groq SDK not installed` | Missing optional dependency | `pip install -e ".[agents]"` |
| Agent loops forever without completing | LLM reasoning issue | Try increasing `--max-steps`; try a different LLM provider; check that the expected tools are visible in ToolHive (`tools/list`); refresh after MCP image upgrades |
| `docker: Cannot connect to the Docker daemon` | Docker not running | Start Docker Desktop |
| Container starts but shows 0 tools | MCP server failed to start | Check container logs: `docker logs <container-id>`; verify the image built successfully |

---

## Running tests

The test suite covers the agent and MCP server without making any real API calls:

```bash
pip install -e ".[dev,agents]"
pytest tests/test_toolhive_agent.py -v
```

Expect every test collected from `tests/test_toolhive_agent.py` to pass (names and count change as the suite evolves). If a test fails, re-run with `-v` and compare against the current definitions in that file.

---

## File layout (`agents`)

```
node-wire/
├── Dockerfile                              ← Docker image for ToolHive
├── pyproject.toml                          ← [agents] extras added
├── sample.env                              ← env var reference
└── src/
    └── agents/
        ├── __init__.py
        ├── mcp_entrypoint.py               ← MCP stdio server (manifest; all MCP connectors)
        ├── toolhive.py                     ← ReAct agent + CLI
        ├── llm_factory.py                  ← Provider factory
        └── providers/
            ├── groq_provider.py            ← Default (Groq)
            ├── openai_provider.py
            ├── gemini_provider.py
            └── anthropic_provider.py
```

---

## Related documentation

- [Installation Guide](installation.md) — Full platform setup guide
- [Configuration Guide](configuration.md) — Environment variables and settings
- [google_drive_connector.md](google_drive_connector.md) — Google Drive service account setup and REST API reference
