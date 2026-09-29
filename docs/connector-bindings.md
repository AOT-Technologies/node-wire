<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Connectors on REST, MCP and gRPC

How an action you wrote (see [Build a connector](connectors-build.md)) becomes an HTTP route, an MCP tool,
and a gRPC method — and what each binding does to the arguments on the way in.

All three share one invoke seam (`src/bindings/invoke.py`); see
[Architecture](architecture.md) for where that sits in the layering.

---

The factory and manifest drive all bindings. Once a connector is registered and `load()` is called, REST, gRPC, and MCP discover enabled connectors according to `exposed_via`.

## REST binding

`src/bindings/rest_api/app.py` calls `build_manifest(connectors)` and registers a `POST /connectors/{connector_id}/{action}` route for every manifest entry:

```
POST /connectors/google_drive/files.list
Content-Type: application/json

{ "page_size": 10, "query": "name contains 'report'" }
```

The `action` field in the body is optional for REST — the binding injects it from the URL path (see `src/node_wire_runtime/ingress.py`). Per-action **argument normalizers** (`mcp_normalize` on each action) run on the JSON body the same way as MCP, so LLM-friendly aliases work for REST as well. If the body includes an `action` field, it **must** match the path segment; otherwise the API returns **400**.

The runtime then performs full Pydantic validation and returns a `ConnectorResponse`.

**Response envelope:**

```json
{
  "success": true,
  "data": {
    "raw": { "files": [{ "id": "...", "name": "...", "mimeType": "..." }], "nextPageToken": null },
    "description": "Successfully executed files.list"
  },
  "trace_id": "4f3a...",
  "error_code": null,
  "error_category": null,
  "message": null
}
```

HTTP status codes are mapped from `ErrorCategory`:

| `ErrorCategory` | HTTP status |
|-----------------|-------------|
| `BUSINESS` | 400 |
| `AUTH` | 401 |
| `RETRYABLE` | 503 |
| `FATAL` / other | 500 |

## MCP binding

`src/bindings/mcp_server/server.py` registers one **MCP tool** per manifest entry. Advertised tool names are `{connector_id}_{action}` with dots in the action replaced by underscores (e.g. `google_drive_files_list`, `google_drive_files_upload`) so OpenAI/NVIDIA function schemas accept them. Legacy dotted names still invoke.

The MCP server calls `connector.run(args_dict)` and serialises the `ConnectorResponse` as the tool result.

The **resolved action** (from the advertised or legacy tool name) is authoritative: after normalizers run, the binding sets `action` from that name. A conflicting `action` in the payload is rejected (see `enforce_authoritative_action` in `src/node_wire_runtime/ingress.py`).

Optional per-action **argument normalizers** (`mcp_normalize` on `@sdk_action` / `SdkActionSpec`) run before `connector.run` to map LLM aliases to canonical fields. Actions default to **strict** JSON Schema (`additionalProperties: false`); set `alias_tolerant=True` only where extra keys must pass MCP SDK validation before normalization.

Published **`input_schema` omits the `action` property** (manifest contract v2+): clients must not rely on sending `action` inside tool arguments; the MCP tool name (or REST path / gRPC `InvokeRequest.action`) is authoritative.

**FHIR `search_encounter` (Epic/Cerner):** normalizers map root-level `patient` / `patientId` to `patient_id`, and `sort` → `_sort` (via `search_params`). Encounter search **requires** a patient filter (`patient_id` or `patient` in `search_params`) before any outbound FHIR call.

## gRPC binding

`src/bindings/grpc_server/server.py` exposes the `Connector` service. The **`action` field on `InvokeRequest`** is authoritative: after argument normalizers run, ingress rejects a conflicting `action` inside `payload_json` (same rule as REST and MCP). The shared Binding invoke path in `src/bindings/invoke.py` performs factory resolution and `connector.run`.

## Manifest

`build_manifest(connectors)` (from `node_wire_runtime.manifest`) is the single source of truth for both bindings (by default it strips `action` from each entry’s `input_schema`). It returns one entry per `@sdk_action`:

```python
[
  {
    "connector_id": "weather",
    "action": "current_weather",
    "input_schema": { ... },   # JSON Schema from CurrentWeatherInput (action not required)
    "output_schema": { ... },  # ConnectorResponse envelope; data typed to the action output model (nullable on errors)
  },
  {
    "connector_id": "google_drive",
    "action": "files.upload",
    ...
  }
]
```

---

## Optional: MCP under `src/agents/` (ToolHive / stdio)

The repo also ships **stdio MCP servers** for agents and ToolHive under `src/agents/` (e.g. `python -m agents.mcp_entrypoint`, per-connector modules). Those are separate from `MODE=MCP` on `node-wire`. Wiring a connector in `config/connectors.yaml` does not by itself add a ToolHive image. Which deployment to use: [MCP overview](mcp.md#which-mcp-path). What a dedicated image needs: [Packaging — Tier 3](packaging.md#tier-3-standalone-mcp-server-optional).

---

## Related documentation

| Doc | When to read it |
|-----|-----------------|
| [Build a connector](connectors-build.md) | Writing the connector these bindings expose |
| [connector-reference.md](connector-reference.md) | `connectors.yaml` keys and the factory API |
| [architecture.md](architecture.md) | Where the bindings sit in the three-layer design |
| [mcp.md](mcp.md) | MCP transports, tool search, multi-tenancy |
