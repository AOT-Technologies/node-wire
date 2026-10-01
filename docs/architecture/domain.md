<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Domain

The words the rest of the docs use, each with one meaning. Read the
[architecture overview](../architecture.md) first. Read [Seams](seams.md) next.

## Connectors and actions

| Term | Meaning |
|---|---|
| **Connector** | An adapter for one external system, such as `google_drive`, `stripe` or `fhir_epic`. It is a `BaseConnector` subclass in its own package, `src/node_wire_<connector_id>/`, and it registers through the `node_wire.connectors` entry-point group. Generated connectors subclass `RestConnector`. |
| **Connector id** | The connector's stable name (`connector_id`). The same string appears in `connectors.yaml`, `NW_ALLOWED_CONNECTORS`, the entry-point key, REST paths and MCP tool names. |
| **Action** | One operation a connector exposes (`files.list`, `charge`, `read_patient`). It is declared with `@nw_action` / `@sdk_action` or an `action_specs` entry. Each action has a Pydantic input model with an `action: Literal[...]` discriminator. |
| **Manifest** | The list of `(connector_id, action, input_schema, output_schema)` entries built by `build_manifest()`. REST routes, MCP tools and gRPC dispatch are all derived from it ([bindings](../connector-bindings.md#manifest)). |
| **Tool name** | An action's MCP name: `<connector_id>_<action>`, with dots in the action replaced by underscores (`google_drive_files_list`). Legacy dotted names still work on `tools/call`. |
| **Normalizer** | An optional per-action function (`mcp_normalize`) that maps LLM-friendly argument aliases to canonical fields before validation. It runs on REST and MCP. |

## Results and errors

| Term | Meaning |
|---|---|
| **`ConnectorResponse`** | The envelope every `run()` returns: `success`, `data`, `trace_id`, `error_code`, `error_category`, `message`. Actions never return raw vendor objects, and failures never escape as exceptions. |
| **`ErrorCategory`** | The error taxonomy: `RETRYABLE`, `BUSINESS`, `AUTH`, `FATAL`. Bindings translate it into an HTTP status, a gRPC reply or an MCP tool error ([Errors](../errors.md)). |
| **Error code** | A stable string such as `GDRIVE_RATE_LIMIT` or `MISSING_TENANT`. A connector's own codes come from its `error_map`, scoped to that connector's id; the runtime-wide ones, for failures inside or before a run, are listed in [Errors](../errors.md). |

## Tenancy

| Term | Meaning |
|---|---|
| **Tenant** | The caller's organisation. With multi-tenancy off, every call is `__default__` (`DEFAULT_TENANT`). With it on, the binding resolves the tenant from the request ([Tenancy](tenancy.md#how-the-tenant-is-resolved)). |
| **Named config** | One configuration of a connector for a tenant, such as `sandbox` or `live`, holding a `config` block, an `auth` block and secrets. A tenant can hold several. The first one created is the default. |
| **Entitlement** | A tenant may use a connector only if it has at least one config for it. There is no separate allow list. |
| **Config store** | `ConnectorConfigStore`: the runtime store of named configs, keyed by `(tenant, connector, config_name)`. It is loaded from `connectors.yaml` (as `__default__`) and from `NW_TENANTS_PATH`. |
| **Tenant pin** | The tenant a factory-built connector instance is bound to. `run()` uses the pin. A different `tenant_id` returns `TENANT_MISMATCH` without calling out. Not the same as the MCP *session* pin (`NW_TENANT_ID`). |

## Callers and policy

| Term | Meaning |
|---|---|
| **Principal / scopes** | The caller identity a binding passes to `run()`, from an API key or a JWT. |
| **Scope** | A permission string. By convention an action needs `mcp:<connector_id>.<action>`. `NW_MCP_ACTION_SCOPE_MAP_JSON` can override this per action. |
| **Scope policy** | The allow/deny decision made before an action runs, by `ScopePolicyHook` ([Seams](seams.md#policyhook)). |
