<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Troubleshooting guide

## Common errors & fixes

| Problem | Likely cause | Fix |
|---------|--------------|-----|
| **Port 8000 in use** | Another process is using the default REST port. | Set `PORT=8001` (or any free port) before starting the platform. |
| **`404 Not Found` on `/connectors/<connector-id>/<action>`** | No REST route was created for the connector at startup: it is disabled, its `exposed_via` omits `rest`, or it is not registered (missing from `NW_ALLOWED_CONNECTORS` or not installed). | Confirm `enabled: true` and that `exposed_via` includes `rest` in `config/connectors.yaml`, that the connector is in `NW_ALLOWED_CONNECTORS`, then restart. |
| **Auth Failure (Google Drive)** | Incorrect credential format. | In ToolHive, `GOOGLE_DRIVE_SA_JSON` must be the JSON **contents**. Locally, it can be an absolute path. |
| **"Invalid port: PORT"** | `TOOLHIVE_MCP_URL` still holds the literal `PORT` placeholder from `sample.env` (`http://localhost:PORT/mcp`). | Replace `PORT` with the real port of your ToolHive MCP server, for example `http://localhost:8081/mcp`. |
| **No connectors loaded** | `NW_ALLOWED_CONNECTORS` is missing. | **Required.** Set `NW_ALLOWED_CONNECTORS` to a comma-separated list of connectors to enable. |
| **`400 MISSING_TENANT`** | `NW_MULTITENANCY_ENABLED=true` but the request carried no tenant (no `X-Tenant-ID` header / JWT claim / `NW_TENANT_ID` pin). | Send `X-Tenant-ID` (REST/gRPC), set `NW_TENANT_ID` (MCP stdio), or add a `__default__` tenant to `tenants.yaml`. |
| **`403 TENANT_IDENTITY_MISMATCH`** | A JWT `tenant` claim or tenant-bound key disagrees with the caller-supplied header/session tenant; or the caller asked for `__default__` (operator-only); or a tenant-less credential sent a tenant header without `NW_TRUSTED_TENANT_HEADER=true`. | Use a token whose tenant claim matches the `X-Tenant-ID` header (or the session's selected/pinned tenant) — never silently overridden. |
| **`tenant secret not found: <tenant>/<connector>/...`** | No secret set for that tenant/connector (and config, if named) combination. | Add `NW_{TENANT}_{CONNECTOR}_{KEY}` (or `NW_{TENANT}_{CONNECTOR}_{CONFIG}_{KEY}` for a named config) as an env var, or add it under `secrets:` in `tenants.yaml`. |
| **`Config 'X' is not defined for connector 'Y'`** | `nw_select_config` (or a per-call `config_name`) named a config that doesn't exist for that connector on the selected tenant. | Use a config name that exists on every connector you call, or add it via `tenants.yaml` / REST. |

Multi-tenancy reference: [Tenancy](architecture/tenancy.md) (behaviour), [Configuration — Multi-tenancy](configuration.md#multi-tenancy) (variables).

---

## Logging & debugging

### REST API

Check the console output where `uv run node-wire` is running. It logs incoming requests and standard error taxonomy mappings.

### MCP (stdio)

In `stdio` mode, the server communicates over standard I/O. Any `print()` statements in the code will break the protocol. Use Python's `logging` module to log to `stderr` or a file.

### OpenTelemetry

If configured, check your OpenTelemetry collector (for example, Jaeger) for traces with `trace_id` from the `ConnectorResponse`.

## Related

- [Errors](errors.md): every error code, its category, and what each surface returns.
- [Configuration](configuration.md): every `NW_*` variable and its default.
