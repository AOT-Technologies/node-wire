<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Errors

Every failed call, on every surface, carries the same four facts: an error **code**, its
**category**, a **message** and a **trace id**. The runtime decides all four. A binding only
turns them into its transport: an HTTP status, a gRPC reply, an MCP `isError` result.

## The envelope

A failure is a `ConnectorResponse` with `success: false`:

```json
{
  "success": false,
  "data": null,
  "error_code": "MISSING_TENANT",
  "error_category": "AUTH",
  "message": "X-Tenant-ID is required when multitenancy is enabled",
  "trace_id": "3b5d519f-7a6f-439b-937d-12615e0706e6",
  "details": null
}
```

`details` is set for some codes, e.g. `VALIDATION_ERROR` lists `{"loc": [...], "msg": ...}` per
bad field. On MCP the same failure also reads as one line:

```text
MISSING_TENANT [AUTH]: X-Tenant-ID is required when multitenancy is enabled (trace_id=3b5d519f-7a6f-439b-937d-12615e0706e6)
```

## Categories

| Category | Meaning | HTTP status (REST, connector failures) |
|---|---|---|
| `BUSINESS` | The request is wrong; fix it and retry | 400 |
| `AUTH` | Not authenticated, or not allowed | 401 |
| `RETRYABLE` | Transient; retrying may succeed | 503 |
| `FATAL` | The server or upstream is misconfigured or broken | 500 |

## Where a code comes from

- **Inside a connector run**, `ErrorMapper` maps the exception: first the connector's own
  `error_map` (e.g. `GDRIVE_RATE_LIMIT`), then the runtime-wide codes below. An exception
  neither knows is reported under its class name, with `FATAL`.
- **Before the connector runs** (arguments, tenant, config, rate limit, unknown tool), the binding
  raises `NodeWireError(code, message)`, and `node_wire_runtime.errors.reject()` builds the
  envelope, gives it a trace id and logs it. Bindings never invent a code.

## Runtime-wide codes

`node_wire_runtime.ErrorCode`; each code's category is `node_wire_runtime.errors.CATALOGUE`.
Codes shipped in 1.0.0 keep their name and category through 1.x.

| Code | Category | When |
|---|---|---|
| `VALIDATION_ERROR` | `BUSINESS` | Arguments don't fit the tool or the action's input model (every bad field is named; `details` lists them) |
| `INVALID_PAYLOAD` | `BUSINESS` | gRPC only: its 1.0.0 name for a payload rejected before the connector ran (elsewhere `VALIDATION_ERROR`); kept through 1.x |
| `INVALID_JSON` | `BUSINESS` | gRPC: `payload_json` is not JSON |
| `UNKNOWN_TOOL` | `BUSINESS` | No such tool on this server, an ambiguous name, or a tool this server doesn't enable (tool search, tenant tools without multitenancy) |
| `CONNECTOR_NOT_AVAILABLE` | `BUSINESS` | The connector isn't served on this surface, or isn't allowed on this server |
| `RATE_LIMIT_EXCEEDED` | `RETRYABLE` | The global or per-identity rate limiter refused the call |
| `MISSING_TENANT` | `AUTH` | Multitenancy is on and the call names no tenant |
| `TENANT_NOT_ALLOWED` | `AUTH` | The tenant is unknown, or excluded by `NW_MCP_ALLOWED_TENANTS` |
| `TENANT_PIN_LOCKED` | `AUTH` | `nw_select_tenant` while `NW_MCP_TENANT_PIN_LOCKED=true` |
| `TENANT_MISMATCH` | `AUTH` | A connector instance pinned to one tenant was run for another |
| `TENANT_IDENTITY_MISMATCH` | `AUTH` | The JWT's tenant claim disagrees with the requested tenant |
| `CONFIG_NOT_FOUND` | `AUTH` | No config for this tenant and connector, or an unknown config name (the two are indistinguishable on purpose) |
| `CONFIG_NAME_CONFLICT` | `BUSINESS` | Creating a config whose name already exists |
| `CONFIG_DEFAULT_REQUIRED` | `BUSINESS` | Deleting the default config without naming a new one |
| `CONFIG_INVALID` | `BUSINESS` | Any other config-store refusal |
| `POLICY_DENIED` | `AUTH` | The policy hook (e.g. the MCP scope policy) refused the action |
| `PROXY_AUTH_FAILED` | `AUTH` | stacklok servers: the request lacks the ToolHive tenant proxy's `X-NW-Proxy-Secret` |
| `UPSTREAM_TOKEN_MISSING` | `AUTH` | stacklok servers: ToolHive forwarded no `Authorization: Bearer` credential |
| `SECRET_NOT_FOUND` | `FATAL` | The server lacks a secret it is configured to read (a misconfiguration, so REST answers 500) |
| `TENANT_SECRET_NOT_FOUND` | `FATAL` | The same, for a tenant's secret |

## Per surface

| Surface | A failed call |
|---|---|
| REST | The envelope, plus `detail` (the message) for clients of the plain FastAPI errors. Connector failures: status from the category (table above). Refused before running: the status REST has always used, e.g. 400 `MISSING_TENANT`, 403 `CONFIG_NOT_FOUND` (fail-closed), 404 `CONNECTOR_NOT_AVAILABLE`, 409 `CONFIG_NAME_CONFLICT`, 429 `RATE_LIMIT_EXCEEDED` with `Retry-After` |
| gRPC | `InvokeResponse` with `success`, `error_code`, `error_category`, `message`, `trace_id` |
| MCP, node-wire host | `isError: true`; the text is the one-line form; `structuredContent` is the envelope (the tools' declared output schema) |
| MCP, stacklok-built server | `isError: true`; the text is the one-line form (after FastMCP's `Error executing tool <name>: ` for failures inside a tool) |

Ingress authentication is the exception for now: the MCP host reports `MCP_AUTH_REQUIRED`,
`MCP_AUTH_INVALID` or `MCP_AUTH_NOT_CONFIGURED` (category `AUTH`), and REST and gRPC answer with a
status only. One `AUTH_*` set across surfaces is planned for 2.0.

## Following a failure

The trace id is the key into the logs. Every failed call writes one line under it:

| `audit_event` | Logged by | For |
|---|---|---|
| `invocation_failure` | `runtime.base_connector` | the connector ran and failed |
| `invocation_validation_failure` | `runtime.base_connector` or `runtime.errors` | the input or arguments didn't validate |
| `invocation_rejected` | `runtime.errors` | the call was refused before the connector ran |
| `policy_denial` | `runtime.base_connector` | the policy hook refused the action |

With the `grafana/` stack, paste the trace id into the dashboard's `trace_id` box, or query
Loki: `{service_name="<service>"} | trace_id="<trace id>"`.
