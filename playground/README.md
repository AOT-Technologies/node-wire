<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Node Wire Playground

This folder contains a fully functional playground for **Node Wire**, showcasing how it orchestrates complex workflows across disparate systems like Electronic Health Records (EHR) and IT Service Management (ITSM) tools.

## 🚀 Overview

The demo provides a modern, interactive web interface to trigger, monitor, and verify end-to-end automation scenarios. It highlights the platform's ability to handle data mapping, authentication, and resource creation with transparency.

### Core Technologies
- **Frontend**: Vanilla HTML5, CSS3 (Glassmorphism), and Javascript.
- **Backend API**: FastAPI (Python) serving orchestration logic via `playground/scenarios.py`.
- **Connector layer**: Uses Node Wire connectors (`fhir_epic`, `fhir_cerner`, `http_generic`, and others) via the platform REST API and `ConnectorFactory` (see [docs/connectors.md](docs/connectors.md)).

---

## 📖 Scenarios & Implementation

### 🏥 Scenario 1: EHR Orchestration (Epic FHIR)
This scenario automates the process of synchronizing clinical notes from a third-party application into a patient's chart in Epic.

*   **Logic Flow**:
    1.  **Patient Discovery**: Reads patient details (Name, DOB, Patient ID) to verify identity in the EHR.
    2.  **Encounter Identification**: Searches for a matching "Finished" encounter (medical visit) for that patient.
    3.  **Clinical Note Upload**: Automatically encodes the clinical note as Base64 and creates a `DocumentReference` resource in Epic.
    4.  **Verification**: Re-queries the EHR to confirm the document's existence and displays the raw FHIR JSON response.
*   **Implementation**: Uses the `fhir_epic` connector, handling complex FHIR resource schemas and mapping internal data to US Core standards.

### 🛠️ Scenario 2: IT Ops Automation (Generic HTTP)
This scenario demonstrates how the platform can integrate with any REST-enabled legacy system or internal tool without requiring a specific connector.

*   **Logic Flow**:
    1.  **Payload Formatting**: Transforms user input into a standardized ITSM ticket schema.
    2.  **Dispatch Webhook**: Dispatches the payload via a standard REST `POST` request.
    3.  **Verification**: Simulates upstream acceptance and generates a unique tracking ID.
    4.  **Audit Log**: Triggers a background task to record the transaction in the system audit log.
*   **Implementation**: Uses the `http_generic` connector. In this demo, it targets `httpbin.org/post` to echo and verify the dispatched data, showcasing universal connectivity.

### 🛠️ Scenario 3: Cerner FHIR R4 Orchestration
This scenario demonstrates advanced clinical note orchestration for Oracle Health (Cerner) legacy systems, handling proprietary coding and strict validation rules.

*   **Logic Flow**:
    1.  **Identity Verification**: Verifies patient identity (e.g., Nancy Smart) using the `fhir_cerner` connector.
    2.  **Medical Visit Sync**: Locates specific encounters compatible with clinical documentation (e.g., `97957281`).
    3.  **Secure Clinical Sync**: Handles Cerner's specific requirements, including **CodeSet 72** document types, mandatory `docStatus`, and synchronized clinical periods to avoid temporal validation errors.
    4.  **EHR Verification**: Confirms the document creation by querying for the specific resource ID, ensuring it's properly indexed in the patient's record.
*   **Implementation**: Uses the `fhir_cerner` connector, demonstrating automated handling of Cerner's strict 422/400 validation rules (e.g., numeric practitioner IDs and specific search parameter combinations).

### 🔒 Scenario 4: Secure Document Archival (Google Drive Vault)
This scenario demonstrates secure archival of clinical documentation and incident reports into an access-controlled Google Drive Vault.

*   **Logic Flow**:
    1.  **Metadata Formatting**: Prepares strict schema mapping for the archival request including folder and recipient metadata.
    2.  **Upload to Secure Vault**: Pushes the plain-text confidentiality payload to Google Drive using `files.upload`.
    3.  **Establish Data Access**: Dynamically provisions reader IAM permissions for the designated recipient email using `permissions.create`.
    4.  **Verify Integrity**: Constructs a secure web-view link and retrieves access logs through `files.get`.
*   **Implementation**: Uses the `google_drive` connector loaded via `service_account.json` credentials, demonstrating how non-healthcare cloud platforms integrate seamlessly into the orchestration pipeline alongside FHIR standards.

### 🤖 Scenario 5: AI Agent Orchestration (MCP)
This scenario demonstrates the platform's highest level of abstraction: an autonomous AI Assistant that uses the **Model Context Protocol (MCP)** to orchestrate complex healthcare workflows through natural language.

*   **Logic Flow**:
    1.  **Autonomous Reasoning**: The agent parses user intent (e.g., "Get Nancy Smart's record and email it to her") using a Large Language Model (LLM).
    2.  **Dynamic Tool Selection**: Automatically selects and sequences tools from the **Node Wire MCP Server**, including Cerner FHIR, Google Drive, and SMTP.
    3.  **Guardrailed Execution**: Follows strict healthcare-specific guardrails, asking for missing patient IDs or confirmation before performing sensitive actions.
    4.  **Transport-aware Interaction**: Shows the active MCP transport in the chat panel and adjusts rendering behavior to match it.
*   **Implementation**: Leverages the `agents` module, providing a unified interface for LLMs to interact with any connector in the platform via a standard MCP bridge.

### Scenario 6: External Patient Viewer (Read-Only Retrieval)
This scenario loads a source EHR chart on demand for target viewer workflows without duplicating chart data or creating new FHIR resources.

*   **Logic Flow**:
    1.  **Patient Resolution**: Uses a direct FHIR Patient ID when available, or resolves identity with given name, family name, and optional birthdate.
    2.  **Demographics Retrieval**: Calls `read_patient` against the selected source EHR and displays the resolved patient identity.
    3.  **Encounter Retrieval**: Calls `search_encounter` for the resolved patient with a configurable result limit.
    4.  **Document Metadata Retrieval**: Calls `search_document_reference` for available document metadata. When no `DocumentReference` records are returned, the workflow presents encounters as lightweight fallback document rows.
    5.  **Chart Assembly**: Produces a unified external chart view containing demographics, encounters, documents, source system, trace ID, and read-only status.
*   **Implementation**: Uses the existing Epic and Cerner FHIR connectors through `playground/scenarios.py` and the input schema in `playground/ext_patient_viewer/schema.py`. The workflow calls only read/search actions and reports `0 Writes` in the UI.
*   **Endpoint**: `POST /scenarios/external-patient-viewer`
*   **Supported Sources**: Epic FHIR R4 and Cerner FHIR R4.

#### MCP transport behavior in the playground

The Agentic Workflow panel displays the active transport as a pill:

- `Transport: stdio`: the browser calls `/scenarios/agent-chat`. The UI shows the loader while the backend agent completes, then renders tool cards and the final response together.
- `Transport: Streamable HTTP`: the browser calls `/scenarios/agent-chat-stream`. Tool cards appear as each MCP tool finishes, and the final answer is appended progressively as streamed chunks arrive.

Set the mode before starting the REST API:

```powershell
# Playground agent: in-process MCP (recommended for local MT testing)
$env:MODE="API"
$env:NW_MCP_TRANSPORT="stdio"
# Leave TOOLHIVE_MCP_URL empty unless ToolHive / a separate MCP server is running
uv run node-wire
```

```powershell
# Streamable HTTP UI + remote MCP proxy (requires something listening on the URL)
$env:MODE="API"
$env:NW_MCP_TRANSPORT="streamable-http"
$env:TOOLHIVE_MCP_URL="http://127.0.0.1:8081/mcp"
# In another terminal: start MCP — see docs/mcp.md
uv run node-wire
```

After changing `NW_MCP_TRANSPORT` / `TOOLHIVE_MCP_URL`, restart the backend and hard refresh the browser so the latest `app.js` and transport status are loaded.

**Modes (do not confuse):**

| Goal | What to run |
|------|-------------|
| Playground + connector scenarios + Agent (tenant-aware) | `MODE=API` → `http://127.0.0.1:8000/playground/` |
| Standalone MCP tools (Inspector / Claude) | `python -m agents.mcp_entrypoint` (`NW_MCP_TRANSPORT=stdio` or `streamable-http` on `:8081`) — see [docs/mcp.md](../docs/mcp.md) |
| Full binding as MCP process | `MODE=MCP` with same transport vars |

If `TOOLHIVE_MCP_URL` points at `:8081` but nothing is listening, Agent chat fails with `All connection attempts failed`. Clear the URL or start an MCP server; by default the playground falls back to **in-process** MCP when the proxy cannot list tools.

#### Multitenancy (`NW_MULTITENANCY_ENABLED`)

Defaults to off (legacy single-tenant). When enabled:

```powershell
$env:NW_MULTITENANCY_ENABLED="true"
uv run node-wire
```

- **Tenant ID required**: connector/scenario calls without `X-Tenant-ID` return **400**. Explicit `__default__` is allowed.
- **Header**: Tenant dropdown (existing tenants) and Config dropdown appear when multitenancy is on. Header **Add config** is hidden; use **Add config** on each System Connector page.
- **Config dropdown**: lists configs for the **active connector** only under the selected tenant. Switching connectors clears a name that does not exist for the new connector.
- **Agentic Workflow**: sends the same `X-Tenant-ID` (and optional `config_name` query) as the **starting** pin. The agent can then `nw_select_tenant` / `nw_select_config`; the chat response includes the effective tenant/config and the header dropdowns update to match. Local agent MCP runs **in-process** against the playground factory.
- **MCP config discovery**: When MT is on, standalone MCP also loads `tenants.yaml` and exposes `nw_list_tenants`, `nw_select_tenant` (returns that tenant’s configs), `nw_list_configs`, and `nw_select_config`. One select applies to every connector on that MCP process. Env/`X-Tenant-ID` is the default; chat can switch unless `NW_MCP_TENANT_PIN_LOCKED=true`. Configs are still created only via Add config / REST / YAML — not via MCP. Rebuild generated MCP images so vendored `server.py` includes these tools.
- **Per-connector Add config**: On a connector page, **Add config** opens a modal for that connector only (tenant free-text for new tenants, config name, default flag, and varying credentials). Each named config has its **own** credential vault (`NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}`). Shared host env (e.g. `EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`) is copied into that config’s secret overlay when omitted. Persist file: gitignored `config/tenants.yaml` (holds secrets — do not commit).
- **Tenant in logs**: When multitenancy is on, server INFO lines include `tenant_id` / `config_name` for Agent chat, connector scenarios, config mutations, REST connector calls, and MCP tool resolution. The playground Technical Audit panel also prints Tenant/Config for those actions.
- **Instance pin**: `factory.get(tenant_id=...)` pins the connector; `run()` may omit `tenant_id`. Mismatch returns `TENANT_MISMATCH`.

#### Testing the MCP server with Inspector

Use MCP Inspector to validate tools outside the playground:

```powershell
npx @modelcontextprotocol/inspector
```

For stdio inspection:

```powershell
$env:NW_MCP_TRANSPORT="stdio"
npx @modelcontextprotocol/inspector python -m agents.mcp_entrypoint
```

For streamable HTTP inspection, start the MCP server first:

```powershell
$env:NW_MCP_TRANSPORT="streamable-http"
$env:NW_MCP_HOST="127.0.0.1"
$env:NW_MCP_PORT="8081"
$env:NW_MCP_PATH="/mcp"
python -m agents.mcp_entrypoint
```

Then open Inspector, select `Streamable HTTP`, connect to `http://127.0.0.1:8081/mcp`, run `List Tools`, and call a safe tool with valid JSON arguments.

---

## 🛠️ Advanced Platform Features

### 🛡️ Global Resilience Engine
Every request in the platform is now governed by an intelligent auto-retry mechanism.
- **Exponential Backoff**: Automatically retries failed requests with increasing delays (1s, 2s, 4s...) to handle transient network issues or rate limits.
- **Real-time Visibility**: The UI displays retry counts for each step, providing transparency when the platform is actively recovering from a system error.

### 🔍 Intelligent Error Classification
The platform distinguishes between different failure modes to provide actionable feedback:
- **BUSINESS**: Data validation or permission issues (e.g., "Patient not found").
- **RETRYABLE**: Transient system errors that the resilience engine can handle.
- **FATAL**: Critical infrastructure failures requiring manual intervention.
Errors are color-coded and clearly labeled in the "Technical Audit" panel.

---

## 🧪 Testing with Real Environments

The demo is pre-configured with mock/sandbox endpoints for immediate use. To test with real systems, follow these steps:

### Testing Real Epic/Cerner (EHR)
1.  **Update Config**: Modify `config/connectors.yaml` to point to a real Epic/Cerner Sandbox or Production URL.
2.  **Auth**: Ensure you have valid `CLIENT_ID` and `PRIVATE_KEY` for the EHR's Backend System OAuth2 flow (SMART on FHIR).
3.  **Data**: Use real Patient IDs and Encounter IDs from your target environment.
    - **Cerner Note**: Ensure you use numeric Practitioner IDs (e.g., `593923`) and valid CodeSet 72 codes.

### Testing Google Drive Vault (Manual End-to-End)
To test the Google Drive integration manually, follow these specialized setup steps:
1.  **Service Account**: Create a Service Account in the Google Cloud Console with the **Google Drive API** enabled. Download the JSON key.
2.  **Secret Configuration**:
    *   Place the JSON key file somewhere safe on your machine (e.g., `/path/to/service_account.json`).
    *   Update your `.env` file: `GOOGLE_DRIVE_SA_JSON=/path/to/service_account.json`.
    *   *Note: The platform now supports direct file paths for easier local configuration.*
3.  **Permissions**: If using a specific **Vault Folder ID**, ensure that folder is shared with the Service Account's email address (found in the JSON) with "Editor" or "Manager" permissions.
4.  **Workflow Verification**:
    *   **Direct Upload**: Drag a PDF or Image into the "Upload File" zone. Verify the file appears in the drive with correct metadata.
    *   **Note Archival**: Switch to "Write Note", type a clinical summary, and verify it is archived as a `.txt` file.
    *   **IAM Check**: Check the "Share" settings on the archived file in Google Drive; the "Recipient Email" specified in the UI should have been automatically added as a "Reader".

### 🤖 Configuring the AI Agent (Optional)
To enable the AI Agent chat, you need to configure an LLM provider:
1.  **Add API Keys**: Set `GROQ_API_KEY` / `GROQ_MODEL` (default in the playground switcher). Optionally set `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, and `NVIDIA_MODEL` to enable NVIDIA Nemotron in the same dropdown.
2.  **Switch models in the UI**: Use the single `provider/model` selector next to the chat input (defaults to Groq when configured).
3.  **SMTP Setup**: (Optional) Add SMTP credentials (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`) to enable the agent to send emails.
4.  **MCP URL**: In `streamable-http` mode, set `TOOLHIVE_MCP_URL` or `TOOLHIVE_MCP_URLS` to the HTTP MCP endpoint(s). In `stdio` mode, the playground ignores those URLs and uses local stdio.
5.  **Allowed Connectors**: Ensure `NW_ALLOWED_CONNECTORS` in your `.env` includes the connectors used by the agent (e.g. `fhir_cerner,google_drive,smtp`).

---

## 🛠️ How to Run

1.  Navigate to the project root.
2.  Start the FastAPI server:

```bash
# Using uv (recommended)
uv run node-wire

# Using python
python -m bindings_entrypoint
```

3.  Open your browser to `http://localhost:8000/playground/` (or the configured port).
4.  Switch between **EHR**, **IT Ops**, **Cerner**, **Google Drive Vault**, **AI Agent**, **Slack**, **Stripe**, **Salesforce** and **External Patient Viewer** cards to explore the different workflows.
