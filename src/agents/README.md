# Node Wire Agents & MCP Orchestration

This folder contains the core intelligence and orchestration layer of **Node Wire**, enabling autonomous AI agents to interact with healthcare systems and cloud services via the **Model Context Protocol (MCP)**.

## Overview

The `agents` module transforms static connectors (EHR, Google Drive, SMTP) into dynamic, discoverable tools for Large Language Models (LLMs). By following the MCP standard, we provide a unified interface for "ReAct" style agents to perform end-to-end clinical workflows through natural language instructions.

### Key Capabilities
- **Autonomous Reasoning**: Agents can discover available tools and sequence them to achieve complex goals (e.g., "Summarize Nancy Smart's chart and archive it to the patient vault").
- **Multi-System Orchestration**: Bridge the gap between HL7 FHIR standards (Cerner/Epic) and enterprise tools (Google Drive/SMTP).
- **Plug-and-Play LLMs**: Support for multiple flagship models through a unified provider factory.

---

## Core Architecture

### 1. **MCP Server (`mcp_entrypoint.py`)**
Stdio MCP server using the official [Model Context Protocol Python SDK](https://github.com/modelcontextprotocol/python-sdk).
- **Manifest-driven tools**: `McpServer` builds the tool list from connector metadata (`<connector_id>.<action>`) and dispatches via `connector.run()`.
- **Unified entrypoint**: `python -m agents.mcp_entrypoint` exposes every connector enabled for MCP in `config/connectors.yaml`.
- **Per-connector images**: `fhir_cerner_mcp`, `fhir_epic_mcp`, `google_drive_mcp`, and `smtp_mcp` run the same server with a `connector_ids` filter.

### 2. **ToolHive Agent (`toolhive.py`)**
A reference implementation of a ReAct-style agent designed for the **ToolHive** ecosystem.
- **Reference Workflow**: Pre-configured to orchestrate the "Cerner → Google Drive → SMTP" clinical summary pipeline.
- **Hybrid Connection**: Supports connecting via an HTTP/SSE proxy (production) or directly to the local server via `stdio` (development).

### 3. **LLM Provider System (`providers/`)**
A modular factory system supporting diverse LLM backends:
- **Groq** (Default): Optimized for speed with Llama-3-70b.
- **OpenAI**: Industry standard with GPT-4o-mini.
- **Google Gemini**: Large context windows with Gemini-2.0-flash.
- **Anthropic**: High-reasoning capabilities with Claude-3.5-Haiku.

---

## MCP tool naming

Tools are named **`{connector_id}.{action}`** as defined by each connector’s manifest (see `connectors/manifest.py` and `bindings/mcp_server/server.py`). Examples:

| Example tool name | Connector |
| :--- | :--- |
| `fhir_cerner.read_patient` | Cerner FHIR |
| `fhir_epic.read_patient` | Epic FHIR |
| `google_drive.files.upload` | Google Drive |
| `smtp.send_email` | SMTP |

Use **`tools/list`** for the exact names and JSON Schemas your deployment exposes.

---

## ⚙️ Configuration

Configuration is managed via environment variables in your `.env` file.

### **LLM Credentials**
```bash
# Provider Selection: groq | openai | gemini | anthropic
LLM_PROVIDER=groq
GROQ_API_KEY=gsk_...

# Optional: Override default models
GROQ_MODEL=llama-3.3-70b-versatile
```

### **MCP & Orchestration**
```bash
# ToolHive Proxy URL (obtain from ToolHive UI)
TOOLHIVE_MCP_URL=http://localhost:8000/mcp

# Connector Secrets (Injected into MCP Server)
CERNER_CLIENT_ID=...
GOOGLE_DRIVE_SA_JSON=/path/to/service_account.json
SMTP_USERNAME=...
SMTP_PASSWORD=...
```

---

## Usage Guide

### **1. Launch the MCP Server (Local)**
To verify tool discovery and execution via `stdio`:
```bash
python -m agents.mcp_entrypoint
```

### **2. Execute the Autonomous Agent (CLI)**
The agent can be run directly from the command line to perform the reference healthcare workflow.

**Search by Patient Name & Send via Local Server:**
```bash
python -m agents.toolhive --local \
    --patient-family "Smart" \
    --patient-given "Nancy" \
    --recipient-email clinical-team@hospital.org
```

**Direct ID Execution via ToolHive Proxy:**
```bash
python -m agents.toolhive \
    --patient-id 12724066 \
    --recipient-email provider@aot.com \
    --drive-folder-id "1ABC..."
```

---

> [!TIP]
> **Performance Tuning**: For the best results, use **Groq** or **GPT-4o**. These models have high reliability for tool-calling which is critical for the multi-step healthcare workflows supported here.

> [!IMPORTANT]
> **Security Warning**: Ensure that the `service_account.json` file used for Google Drive is excluded from source control and that the Service Account has the minimum necessary permissions (Least Privilege) on the target Drive folders.
