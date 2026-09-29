<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Connector reference

Lookup tables for connector authors and host applications: the `connectors.yaml` schema,
the factory and registry APIs, how to call a connector in-process, and the catalog of what ships today.

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

**Scope policy applies here too.** The factory attaches the same policy hook to every `run()`, however the instance was obtained. With the default `NW_MCP_SCOPE_POLICY_DEFAULT=deny`, a call with no `principal` or `scopes` fails with `POLICY_DENIED`. How to grant scopes, or switch to `allow` for local scripts: [Build a connector — Security](connectors-build.md#security-rest-plugins-secrets).

For a complete, runnable walkthrough — two tenants, the config store, `TENANT_MISMATCH`, and revoking access — see [Use a connector](connectors.md).

**Multi-tenant / named configs:** resolve the tenant in your host, then `await factory.get("google_drive", tenant_id=tenant_id, config_name=name)`. The instance is pinned to that tenant. The contract is in [Tenancy — the pin contract](architecture/tenancy.md#the-pin-contract-hosts-embedding-the-factory).

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

## Connector catalog

Every shipped connector, with the same fields for each. Request and response schemas are not
repeated here. They are generated from each connector's Pydantic models and served live at
`/docs` (Swagger UI) and `/openapi.json`, and the same models drive the MCP tool schemas. Tool names
are `<connector_id>_<action>` with dots replaced by underscores (`google_drive_files_list`).
Environment variables are also listed in [Configuration](configuration.md#connector-secrets).
Per-tenant secrets follow the [tenant secret naming](architecture/tenancy.md#tenant-secrets).

| Connector | Actions | `auth.provider` | Setup guide |
|---|---|---|---|
| [`http_generic`](#http_generic) | `request` | `none` | — |
| [`smtp`](#smtp) | `send_email` | `static_credentials` | — |
| [`stripe`](#stripe) | `charge`, `create_payment_intent`, `create_subscription`, `cancel_subscription`, `issue_refund` | `static_token` | — |
| [`salesforce`](#salesforce) | `create_lead`, `read_lead`, `update_lead`, `delete_lead`, `create_contact`, `read_contact`, `update_contact`, `delete_contact` | `oauth2` (`refresh_token`) | [Salesforce](salesforce_connector.md) |
| [`google_drive`](#google_drive) | `files.list`, `files.get`, `files.create`, `files.upload`, `files.update`, `files.delete`, `permissions.create` | `service_account` (or `upstream_bearer`) | [Google Drive](google_drive_connector.md) |
| [`slack`](#slack) | `post_message`, `send_direct_message`, `upload_file` | `static_token` | [Slack](slack_connector.md) |
| [`fhir_epic`](#fhir_epic-and-fhir_cerner) | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` | `oauth2` (`private_key_jwt`) | — |
| [`fhir_cerner`](#fhir_epic-and-fhir_cerner) | same actions as `fhir_epic` | `oauth2` (`private_key_jwt`) | — |

### `http_generic`

- **Package:** `node-wire-http`, `src/node_wire_http_generic/`. No standalone MCP image.
- **Secrets / settings:** none. The whole request (method, URL, params, body) is the payload.
- **Egress:** only `GET`, `POST`, `PUT`, `PATCH` and `DELETE`. Internal destinations (localhost,
  loopback, private and link-local ranges, metadata endpoints) are rejected. Set
  `NW_HTTP_GENERIC_ALLOWED_HOSTS` to restrict hosts further. Logs drop query strings and fragments.

### `smtp`

- **Package:** `node-wire-smtp`, `src/node_wire_smtp/`. MCP image `nw-smtp`.
- **Secrets / settings:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `FROM_EMAIL`.
- **Egress:** the payload carries only `to`, `subject`, `body` and an optional `from_email`. Relay
  settings are server-side only, so credentials never go to a caller-chosen host. Set
  `NW_SMTP_ALLOWED_HOSTS` in production. `subject` may not contain newlines or control characters.

### `stripe`

- **Package:** `node-wire-stripe`, `src/node_wire_stripe/`. MCP image `nw-stripe`.
- **Secrets / settings:** `STRIPE_API_KEY`, sent raw in `Authorization` (`prefix: ""`).
- **Errors:** `src/node_wire_stripe/logic.py` is the worked `error_map` example
  ([Build a connector](connectors-build.md#optional-error_map-for-errormapper)).

### `salesforce`

- **Package:** `node-wire-salesforce`, `src/node_wire_salesforce/`. MCP image `nw-salesforce`.
- **Secrets / settings:** `SALESFORCE_INSTANCE_URL`, `SALESFORCE_TOKEN_URL`, `SALESFORCE_CLIENT_ID`,
  `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_REFRESH_TOKEN`.
- **Guide:** [Salesforce](salesforce_connector.md) covers configuration and examples.

### `google_drive`

- **Package:** `node-wire-google-drive`, `src/node_wire_google_drive/`. MCP image `nw-google-drive`.
- **Secrets / settings:** `GOOGLE_DRIVE_SA_JSON` (JSON contents, or an absolute path locally), `GOOGLE_DRIVE_FOLDER_ID`.
- **Per-user access:** `upstream_bearer` relays each caller's own Google token
  ([Google Drive — upstream bearer](google_drive_connector.md#upstream_bearer)).
- **Guide:** [Google Drive](google_drive_connector.md) covers service-account setup and the error codes.

### `slack`

- **Package:** `node-wire-slack`, `src/node_wire_slack/`. MCP image `nw-slack`.
- **Secrets / settings:** `SLACK_BOT_TOKEN`. File uploads from disk must sit under
  `NW_SLACK_ATTACHMENTS_DIR` (default `/slack_attachments`).
- **Guide:** [Slack](slack_connector.md) covers bot setup, channel resolution and the error codes.

### `fhir_epic` and `fhir_cerner`

- **Packages:** `node-wire-fhir-epic` (`src/node_wire_fhir_epic/`, MCP image `nw-smartonfhir-epic`)
  and `node-wire-fhir-cerner` (`src/node_wire_fhir_cerner/`, MCP image `nw-smartonfhir-cerner`).
  Both are SMART on FHIR R4 backend services with Epic- and Cerner-specific schemas.
- **Secrets / settings:** Epic: `EPIC_FHIR_BASE_URL`, `EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_KID`,
  `EPIC_PRIVATE_KEY`. Cerner: the same with a `CERNER_` prefix, plus `CERNER_SCOPES`.
- **Privacy:** logs record operation mode, HTTP status and counts, never request parameters or
  FHIR bodies ([Privacy](privacy.md)).
- **Arguments:** normalizers accept common LLM aliases (`patientId` → `resource_id`; root-level
  `patient` / `patientId` → `patient_id` and `sort` → `_sort` on `search_encounter`). Encounter
  search requires a patient filter before any outbound call.

| Action | When to use | Example arguments |
|--------|-------------|-------------------|
| `read_patient` | You have a Patient ID | `{"resource_id": "12724066"}` |
| `search_patients` | No ID, or name-based search | `{"given_name": "Nancy", "family_name": "Smart"}` |
| `search_encounter` | Find medical visits | `{"patient_id": "12724066"}` |

---

## Related documentation

| Doc | When to read it |
|-----|-----------------|
| [Use a connector](connectors.md) | Calling a connector in-process or over REST, per tenant |
| [Build a connector](connectors-build.md) | Writing a connector by hand |
| [connector-bindings.md](connector-bindings.md) | How actions surface on REST / MCP / gRPC |
| [configuration.md](configuration.md) | `NW_*` environment variables |
| [public-api.md](public-api.md) | What is covered by SemVer |
