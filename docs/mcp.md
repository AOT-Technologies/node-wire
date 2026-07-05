<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Model Context Protocol (MCP) in Node Wire

Node Wire integrates with the Model Context Protocol to allow AI agents (like Claude or custom LLM orchestrators) to discover and use connectors as tools.

For **per-connector Docker images and ToolHive registration**, see [mcp-servers.md](mcp-servers.md).

For **outbound OAuth** when connecting to remote authorized MCP servers over HTTP, see [mcp-client-oauth.md](mcp-client-oauth.md).

## Transport Modes

Switch between transports using the `NW_MCP_TRANSPORT` environment variable.

### 1. `stdio` (Default)
Communicates via standard I/O. Best for local development and subprocess-based clients.
**Bash (Linux/macOS):**
```bash
# Using uv
NW_MCP_TRANSPORT=stdio uv run python -m agents.mcp_entrypoint

# Using python
NW_MCP_TRANSPORT=stdio python -m agents.mcp_entrypoint
```

**PowerShell (Windows):**
```powershell
# Using uv
$env:NW_MCP_TRANSPORT="stdio"; uv run python -m agents.mcp_entrypoint

# Using python
$env:NW_MCP_TRANSPORT="stdio"; python -m agents.mcp_entrypoint
```

### 2. `streamable-http`
Native HTTP MCP server using SSE (Server-Sent Events).
**Bash (Linux/macOS):**
```bash
# Using uv
NW_MCP_TRANSPORT=streamable-http NW_MCP_HOST=127.0.0.1 NW_MCP_PORT=8081 NW_MCP_PATH=/mcp uv run python -m agents.mcp_entrypoint

# Using python
NW_MCP_TRANSPORT=streamable-http NW_MCP_HOST=127.0.0.1 NW_MCP_PORT=8081 NW_MCP_PATH=/mcp python -m agents.mcp_entrypoint
```

**PowerShell (Windows):**
```powershell
# Using uv
$env:NW_MCP_TRANSPORT="streamable-http"; $env:NW_MCP_HOST="127.0.0.1"; $env:NW_MCP_PORT="8081"; $env:NW_MCP_PATH="/mcp"; uv run python -m agents.mcp_entrypoint

# Using python
$env:NW_MCP_TRANSPORT="streamable-http"; $env:NW_MCP_HOST="127.0.0.1"; $env:NW_MCP_PORT="8081"; $env:NW_MCP_PATH="/mcp"; python -m agents.mcp_entrypoint
```

### Streaming Features
- **Configurable Buffering (`NW_STREAM_BUFFER_MS`)**: When streaming, output can be buffered to reduce event spam. Set to the duration in milliseconds (e.g., `2000` for a 2-second batching window). Default is `0` (no buffering).
- **Completion Signals**: The core runtime emits structured "done" signals (`stream_completion_log`) via Python logging when streaming ends, allowing package consumers to easily detect when a stream finishes.

---

## Testing with MCP Inspector

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is the best way to validate your MCP tools locally.

### Testing stdio
```bash
npx @modelcontextprotocol/inspector uv run python -m agents.mcp_entrypoint

# Using python
npx @modelcontextprotocol/inspector python -m agents.mcp_entrypoint
```

### Testing streamable-http
1. Start the server (as shown above).
2. Run the inspector:
```bash
npx @modelcontextprotocol/inspector
```
3. In the UI, select **Streamable HTTP** and connect to `http://127.0.0.1:8081/mcp`.

---

## Deployment Modes

Node Wire supports two ways to expose tools via MCP:

### 1. Combined MCP Server
All connectors enabled for MCP in `config/connectors.yaml` are exposed from a single process.
```bash
# Using uv
uv run python -m agents.mcp_entrypoint

# Using python
python -m agents.mcp_entrypoint
```

### 2. Individual MCP Servers
Each connector runs as its own independent MCP server (often in a dedicated Docker container). This is preferred for modular, scalable deployments.
- **Full Guide:** [Individual MCP Servers (Docker)](mcp-servers.md)

---

## FHIR Tool Arguments (Cerner / Epic)

Tool names follow the pattern `fhir_cerner.<action>` and `fhir_epic.<action>`. The MCP server normalizes common LLM aliases (e.g., `patientId` → `resource_id`).

| Action | When to use | Example arguments |
|--------|-------------|-------------------|
| `read_patient` | You have a Patient ID | `{"resource_id": "12724066"}` |
| `search_patients` | No ID, or name-based search | `{"given_name": "Nancy", "family_name": "Smart"}` |
| `search_encounter` | Find medical visits | `{"patient_id": "12724066"}` |

---

## Connector Manifests

Each connector defines a manifest that MCP uses to understand available tools.
- Tool names follow the pattern: `<connector_id>.<action>` (e.g., `google_drive.files.list`).
- The runtime handles argument normalization, so LLM-friendly aliases often work automatically.

## Related Docs
- [Individual MCP Servers (Docker)](mcp-servers.md)
- [ToolHive Agent Scenario](toolhive_agent_scenario.md)
