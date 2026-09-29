<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Connector reference

Lookup tables for connector authors and host applications: the `connectors.yaml` schema,
the factory and registry APIs, how to call a connector in-process, and what ships today.

For walkthroughs, see [Use a connector](connectors.md) and [Build a connector](connectors-build.md).

---

## `config/connectors.yaml`

```yaml
connectors:
  <connector_id>:
    enabled: true          # false → connector not instantiated
    exposed_via:           # controls which bindings surface this connector
      - rest
      - grpc
      - mcp
    # connector-specific keys passed via SecretProvider or connector __init__
```

## `ConnectorFactory` API

| Method | Description |
|--------|-------------|
| `load()` | Reads `connectors.yaml` and bootstraps the runtime config store. Does **not** instantiate connectors — instantiation is lazy. |
| `await get(connector_id, *, tenant_id=None, config_name=None)` | Resolves the tenant's config from the store (the default, or `config_name`), then returns the cached tenant-pinned instance or builds one. Raises `ConfigNotFoundError` when the tenant has no config — existence is entitlement. Omitting `tenant_id` means `__default__`. |
| `store` | The runtime `ConnectorConfigStore` (`create` / `update` / `delete` / `set_default` / `list`). Writes drop the affected cached instances immediately. |
| `get_for_protocol(id, protocol, action=None)` | **Sync, `__default__` tenant only.** Returns the default-tenant instance, or `None` if the connector isn't enabled or isn't exposed for that protocol in YAML. For playground and enumeration — use `get()` for tenant-scoped calls. |
| `is_exposed(connector_id, protocol)` | `True` if the YAML entry lists `protocol` in `exposed_via`. Connectors with no YAML entry (pushed only through the store) are exposed on every protocol. Does not check `enabled`. |
| `list_for_protocol(protocol)` | Default-tenant instances of every enabled, registered connector exposed for `protocol`. |

---

## Connector registry API

`get_connector_registry()` is defined in `base_connector.py` and exported from the top-level `node_wire_runtime` package — it is **not** in `node_wire_runtime.connector_registry`. Use it to read the connector-id → class map after `auto_register()` has imported your `logic` module:

```python
from node_wire_runtime import get_connector_registry
from node_wire_runtime.connector_registry import auto_register

auto_register()  # requires NW_ALLOWED_CONNECTORS
registry = get_connector_registry()  # Dict[str, Type[BaseConnector]]
connector_cls = registry["google_drive"]
```

For the full run pipeline (YAML config, instantiation, protocol routing), use **`ConnectorFactory`** (see [Calling a connector directly](#calling-a-connector-directly-in-process)).

## Calling a connector directly (in-process)

Use `connector.run(dict)` for the full pipeline (validation, policy, retries, error mapping).

Set **`NW_ALLOWED_CONNECTORS`** to a comma-separated list of entry-point names (e.g. `google_drive`) before calling `auto_register()` — without it, `auto_register()` loads nothing (fail-closed).

**Scope policy applies here too.** `ConnectorFactory` installs the same scope hook used by MCP/REST/gRPC on every `run()`, however the instance was obtained (`get` or `get_for_protocol`). With the code / `sample.env` default **`NW_MCP_SCOPE_POLICY_DEFAULT=deny`**, a call with no `principal` or `scopes` fails with `POLICY_DENIED` / `Missing required scope: mcp:<connector>.<action>` — even for local scripts. For local experimentation, set **`NW_MCP_SCOPE_POLICY_DEFAULT=allow`** before constructing the factory, or pass `scopes=("mcp:<connector>.<action>",)` (or `"*"`) into `run()`. See [Security](connectors-build.md#security-rest-plugins-secrets).

For a complete, runnable walkthrough — two tenants, the config store, `TENANT_MISMATCH`, and revoking access — see [Use a connector](connectors.md).

**Multi-tenant / named configs:** Resolve the tenant in your host (header, JWT, etc.), then `await factory.get("google_drive", tenant_id=tenant_id, config_name=name)`. The returned instance is **pinned** to that tenant: omit `tenant_id` on `run()` (recommended) or pass the same id. A different `run(tenant_id=...)` returns `TENANT_MISMATCH` (`ErrorCategory.AUTH`) without running the action. Omitting `tenant_id` on `get` always resolves `__default__`, not the current request tenant.

For composing actions within a connector, use **`self.call_action`**. It routes through **`connector.run`** so **policy hooks**, **resilience**, and the **`ConnectorResponse`** error path apply (including MCP scope policy). It returns the nested action’s **output model** on success (validated from `run()`’s `data`). On policy denial it raises **`PolicyDenied`**, which the outer `run()` maps like any other action failure.

Optional keyword args `principal`, `tenant_id`, and `scopes` override the caller identity for the nested call. When omitted, **`call_action` inherits** identity from the outer `run()` (MCP/REST with JWT or scoped API key), so nested actions receive the same authorization as a direct tool call. On a factory-pinned instance, an explicit nested `tenant_id` must agree with the instance pin.

```python
from node_wire_runtime import BaseConnector, nw_action

@nw_action("upload_then_describe")
async def upload_then_describe(
    self, params: MyInput, *, trace_id: str
) -> GoogleDriveOperationOutput:
    created = await self.call_action(
        "files.create",
        {"action": "files.create", "name": params.name, "mime_type": params.mime_type},
    )
    file_id = created.raw["id"]
    return await self.call_action(
        "files.get",
        {"action": "files.get", "file_id": file_id},
    )
```

---

## Connector inventory

| Connector | Primary actions |
|-----------|-----------------|
| `http_generic` | `request` |
| `smtp` | `send_email` |
| `stripe` | `charge`, `create_payment_intent`, `create_subscription`, `cancel_subscription`, `issue_refund` |
| `salesforce` | `create_lead`, `read_lead`, `update_lead`, `delete_lead`, `create_contact`, `read_contact`, `update_contact`, `delete_contact` |
| `google_drive` | `files.list`, `files.upload`, … (see `action_specs`) |
| `fhir_epic` | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` |
| `fhir_cerner` | Same family as Epic with Cerner-specific schemas |
| `slack` | `post_message`, `send_direct_message`, `upload_file` |

MCP tool names: **`<connector_id>_<action>`** (e.g. `fhir_epic_read_patient`). See [nw-mcp-builder](cli/nw-mcp-builder.md).

---

## Related documentation

| Doc | When to read it |
|-----|-----------------|
| [Use a connector](connectors.md) | Calling a connector in-process or over REST, per tenant |
| [Build a connector](connectors-build.md) | Writing a connector by hand |
| [connector-bindings.md](connector-bindings.md) | How actions surface on REST / MCP / gRPC |
| [configuration.md](configuration.md) | `NW_*` environment variables |
| [public-api.md](public-api.md) | What is covered by SemVer |
