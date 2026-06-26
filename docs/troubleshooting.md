# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Troubleshooting Guide

## Common Errors & Fixes

| Problem | Likely Cause | Fix |
|---------|--------------|-----|
| **Port 8000 in use** | Another process is using the default REST port. | Set `PORT=8001` (or any free port) before starting the platform. |
| **Connector "not configured"** | Connector is disabled or not exposed. | Confirm `enabled: true` and `exposed_via` include your protocol in `config/connectors.yaml`. |
| **Auth Failure (Google Drive)** | Incorrect credential format. | In ToolHive, `GOOGLE_DRIVE_SA_JSON` must be the JSON **contents**. Locally, it can be an absolute path. |
| **"Invalid port: PORT"** | Environment variable not parsed correctly. | Ensure `PORT` or `NW_MCP_PORT` is set to a valid integer (e.g., `8081`). |
| **No connectors loaded** | `NW_ALLOWED_CONNECTORS` is missing. | **Required.** Set `NW_ALLOWED_CONNECTORS` to a comma-separated list of connectors to enable. |

---

## Logging & Debugging

### REST API
Check the console output where `uv run node-wire` is running. It logs incoming requests and standard error taxonomy mappings.

### MCP (stdio)
In `stdio` mode, the server communicates over standard I/O. Any `print()` statements in the code will break the protocol. Use Python's `logging` module to log to `stderr` or a file.

### OpenTelemetry
If configured, check your OpenTelemetry collector (e.g., Jaeger) for traces with `trace_id` from the `ConnectorResponse`.
