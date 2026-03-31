# 🤖 Node Wire Agents & MCP Orchestration

This folder contains the core intelligence and orchestration layer of **Node Wire**, enabling autonomous AI agents to interact with healthcare systems and cloud services via the **Model Context Protocol (MCP)**.

## 🚀 Overview

The `agents` module transforms static connectors (EHR, Google Drive, SMTP) into dynamic, discoverable tools for Large Language Models (LLMs). By following the MCP standard, we provide a unified interface for "ReAct" style agents to perform end-to-end clinical workflows through natural language instructions.

### Key Capabilities
- **Autonomous Reasoning**: Agents can discover available tools and sequence them to achieve complex goals (e.g., "Summarize Nancy Smart's chart and archive it to the patient vault").
- **Multi-System Orchestration**: Bridge the gap between HL7 FHIR standards (Cerner/Epic) and enterprise tools (Google Drive/SMTP).
- **Plug-and-Play LLMs**: Support for multiple flagship models through a unified provider factory.

---

## 🏗️ Core Architecture

### 1. **MCP Server (`mcp_entrypoint.py`)**
A high-performance server built on the [FastMCP](https://github.com/modelcontextprotocol/python-sdk) framework.
- **Dynamic Bindings**: Uses the `ConnectorFactory` to load platform connectors and expose them as MCP tools.
- **Data Protection**: Automatically extracts and summarizes raw FHIR resources to protect patient privacy and reduce LLM token consumption.
- **Flexible Transport**: Defaults to `stdio` transport for seamless integration with ToolHive, Claude Desktop, or custom proxies.

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

## 🛠️ Available MCP Tools

| Tool Name | Description | Connector |
| :--- | :--- | :--- |
| `fhir_cerner_read_patient` | Fetches patient demographics (Name, DOB, ID) from Cerner FHIR R4. | `fhir_cerner` |
| `fhir_epic_read_patient`   | Fetches patient demographics from Epic FHIR R4. (IDs usually start with 'e'). | `fhir_epic` |
| `google_drive_upload_file` | Securely uploads text summaries or reports to a designated folder. | `google_drive` |
| `smtp_send_email` | Dispatches notifications or clinical summaries via secure SMTP. | `smtp` |

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
GOOGLE_DRIVE_SA_JSON=D:\connector-platform\service_account.json
SMTP_USERNAME=...
SMTP_PASSWORD=...
```

---

## 🏃 Usage Guide

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
