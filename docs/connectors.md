<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Connectors guide (`src/node_wire_*`)

This guide explains how **connectors** fit into Node Wire, how to build your own connector, and how the runtime and bindings wire everything together. Connector implementations live under `src/node_wire_<connector_id>/` (e.g. `src/node_wire_google_drive/`); the shared base class lives at **`src/node_wire_runtime/base_connector.py`**.

## How connectors fit into the platform

- **Layer B — Connectors** (`src/node_wire_<connector_id>/`): adapter packages (schemas, logic, optional `registration.py`).
- **Layer C — Bindings** (`src/bindings/`): REST, gRPC, and MCP servers plus `ConnectorFactory` loading from `config/connectors.yaml`.

At startup, bindings call **`node_wire_runtime.connector_registry.auto_register()`**, which loads connector entry points, imports each connector’s `logic` module (registering the class), then imports optional `registration.py` for `ErrorMapper` side effects. **`ConnectorFactory`** resolves connectors from the registry — **do not add per-connector branches in `src/bindings/factory.py`.**

---

## Package layout and registration

Each connector is a **top-level package** under `src/` (e.g. `node_wire_fhir_epic`):

| File | Role |
|------|------|
| `schema.py` | Pydantic input/output models. Each input model has an `action: Literal[...]` discriminator field (often combined into a discriminated union). |
| `logic.py` | Connector class: `BaseConnector` subclass — either explicit `@nw_action` methods, or **`action_specs`** plus an optional `_execute_action_spec` override for SDK dispatch. |
| `action_spec.py` (optional) | Declarative `SdkActionSpec` entries mapping validated models to vendor SDK calls (see Google Drive). |
| `registration.py` | Optional: registers connector-specific exceptions with `ErrorMapper`. |
| `exceptions.py` | Optional: custom exception types. |

At startup, call **`node_wire_runtime.connector_registry.auto_register()`**: it loads entry points in group `node_wire.connectors`, imports each connector's `logic` module (triggering `BaseConnector.__init_subclass__` and `_CONNECTOR_REGISTRY`), then imports optional `registration.py` for `ErrorMapper` side effects.

---

## The unified `BaseConnector`

There is one base class for all connectors: **`BaseConnector`** (`src/node_wire_runtime/base_connector.py`). It handles:

- Input validation via a Pydantic **discriminated union** (the `action` field selects the right model)
- Optional **policy hook** enforcement
- **Retries and circuit breaking** via `with_resilience`
- **Error mapping** via `ErrorMapper`
- OpenTelemetry **tracing**
- A standard **`ConnectorResponse`** envelope

Actions are declared either with the **`@nw_action("name")`** decorator on async methods, or by listing them in **`action_specs`** (the runtime generates equivalent handlers). A connector can have **one or many** actions — there is no separate "single-action" type.

```
flowchart LR
  yaml[connectors.yaml]
  factory[ConnectorFactory.load]
  inst[BaseConnector subclass]
  run[connector.run]
  exec[internal_execute → @nw_action dispatch]
  resp[ConnectorResponse]
  yaml --> factory --> inst --> run --> exec --> resp
```

---

## Building a connector (Google Drive SDK example)

The production **Google Drive** connector (`src/node_wire_google_drive/`) is a good template for wrapping a **vendor Python SDK** (here `googleapiclient` / Drive API v3): service-account auth in `build_client()`, a discriminated union of operations in `schema.py`, and **`action_specs`** so each API surface becomes a manifest action without duplicating boilerplate.

### Step 1 — Define your schemas (`schema.py`)

Each operation is a Pydantic model with an **`action`** field whose type is a `Literal["…"]` unique to that operation. Those models are combined into a **discriminated union** (and often wrapped in `RootModel` for a single top-level validator), which the runtime uses to pick the correct handler.

```python
# src/node_wire_google_drive/schema.py (conceptual excerpt)
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, RootModel


class BaseDriveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilesListOperation(BaseDriveOperation):
    action: Literal["files.list"]
    page_size: int = Field(10, ge=1, le=100)
    query: Optional[str] = None
    fields: Optional[str] = None
    page_token: Optional[str] = None


class FilesUploadOperation(BaseDriveOperation):
    action: Literal["files.upload"]
    name: str
    mime_type: str
    parents: Optional[list[str]] = None
    content: Optional[str] = None
    content_base64: Optional[str] = None


# …other operations (files.create, files.get, …) — see the repo.

_GoogleDriveOperationUnion = Annotated[
    Union[
        FilesListOperation,
        FilesUploadOperation,
        # … FilesCreateOperation, FilesGetOperation, …
    ],
    Field(discriminator="action"),
]

GoogleDriveOperationInput = RootModel[_GoogleDriveOperationUnion]


class GoogleDriveOperationOutput(BaseModel):
    raw: dict
    description: str
```

When a connector only has **one** action, the `action` field is still required — the runtime always validates through the discriminated union.

### Step 2 — Map operations to the SDK (`action_spec.py`)

**`SdkActionSpec`** describes how to turn a validated model into a single SDK call: resource path (`resource_segments`), HTTP-style method name (`method_name`), and how to build `body` / keyword arguments from the model. The full Drive registry lives in [`src/node_wire_google_drive/action_spec.py`](../src/node_wire_google_drive/action_spec.py).

```python
# src/node_wire_google_drive/action_spec.py (illustrative)
from node_wire_runtime.sdk_action_spec import SdkActionSpec

from .schema import FilesCreateOperation, FilesListOperation

# def _build_files_list_kwargs(drive, model): ...

# Real module builds this dict via register helpers — see repo for uploads, permissions, etc.

GOOGLE_DRIVE_ACTION_SPECS: dict[str, SdkActionSpec] = {
    "files.list": SdkActionSpec(
        resource_segments=("files",),
        method_name="list",
        build_kwargs=_build_files_list_kwargs,  # optional: defaults, shared drives flags
        input_model=FilesListOperation,
    ),
    "files.create": SdkActionSpec(
        resource_segments=("files",),
        method_name="create",
        body_from_model={"name": "name", "mime_type": "mimeType", "parents": "parents"},
        constant_kwargs={"fields": "id, name, webViewLink", "supportsAllDrives": True},
        input_model=FilesCreateOperation,
    ),
}
```

`googleapiclient` is **synchronous**. The shared helper **`execute_spec_in_thread`** runs the generated `.execute()` call in a thread pool so the connector’s public API stays async.

### Step 3 — Implement the connector class (`logic.py`)

Subclass `BaseConnector`, set **`connector_id`**, **`output_model`**, and **`action_specs`**. The base class **generates** one async `@nw_action` handler per spec. Override **`_execute_action_spec`** to add logging, thread offload, and translation of vendor exceptions (e.g. `HttpError` → your `error_map` types).

```python
# src/node_wire_google_drive/logic.py (conceptual excerpt)
from __future__ import annotations

import json
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from node_wire_runtime import BaseConnector
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.sdk_action_spec import execute_spec_in_thread

from .action_spec import GOOGLE_DRIVE_ACTION_SPECS
from .exceptions import GoogleDriveAuthError, GoogleDriveRateLimitError  # + other mapped types
from .schema import GoogleDriveOperationOutput


class GoogleDriveConnector(BaseConnector):
    connector_id = "google_drive"
    output_model = GoogleDriveOperationOutput
    action_specs = GOOGLE_DRIVE_ACTION_SPECS

    error_map = {
        GoogleDriveAuthError: (ErrorCategory.AUTH, "GDRIVE_AUTH"),
        GoogleDriveRateLimitError: (ErrorCategory.RETRYABLE, "GDRIVE_RATE_LIMIT"),
        # …
    }

    def build_client(self) -> Any:
        raw_sa = self.secret_provider.get_secret("GOOGLE_DRIVE_SA_JSON")
        info = json.loads(raw_sa)  # or path to a JSON file — see production code
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        return build("drive", "v3", credentials=creds)

    async def _execute_action_spec(
        self,
        action_name: str,
        params: Any,
        *,
        trace_id: str,
        log_extra: dict[str, Any] | None = None,
    ) -> GoogleDriveOperationOutput:
        spec = GOOGLE_DRIVE_ACTION_SPECS[action_name]
        drive = self.get_client()
        try:
            raw = await execute_spec_in_thread(drive, spec, params)
        except HttpError as exc:
            self._translate_and_raise_http_error(exc)
        return GoogleDriveOperationOutput(
            raw=raw,
            description=f"Successfully executed {action_name}",
        )


## Connector Authentication

Node Wire provides a shared **`AuthProvider`** abstraction (`src/node_wire_runtime/auth/`) that handles token acquisition, JWT construction (for SMART on FHIR), caching, and expiry. This ensures that connector logic (`logic.py`) does not need to handle raw credentials or IdP-specific handshake details.

### Using Auth in a Connector

To use authentication, call **`await self.get_auth_headers()`** (inherited from `BaseConnector`). This returns a dictionary of headers (e.g. `{"Authorization": "Bearer <token>"}`) injected by the configured provider.

```python
# logic.py usage
async def read_resource(self, params: In, *, trace_id: str) -> Out:
    base_url = self._get_base_url()
    headers = await self.get_auth_headers()  # Fetched/cached by provider
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base_url}/resource", headers=headers)
        resp.raise_for_status()
    ...
```

### Supported Provider Types

Choose a provider in your **`connectors.yaml`** via the `auth:` block:

| Type | Description |
|------|-------------|
| **`none`** | (Default) No auth headers added. |
| **`static_token`** | Uses a fixed token from a secret (Bearer, Basic, or custom). Supports refresh. |
| **`oauth2`** | Full Client Credentials flow. Supports `private_key_jwt` (RS384) and `client_secret_post`. Handles caching and expiry automagically. |

### Configuration (`connectors.yaml`)

```yaml
connectors:
  fhir_epic:
    enabled: true
    auth:
      provider: oauth2
      grant_method: private_key_jwt
      token_url_secret: EPIC_TOKEN_URL
      client_id_secret: EPIC_CLIENT_ID
      private_key_secret: EPIC_PRIVATE_KEY
      kid_secret: EPIC_KID
      algorithm: RS384

  stripe:
    enabled: true
    auth:
      provider: static_token
      secret_key: STRIPE_API_KEY
```

---
```

Key points:
- **`connector_id`** — unique string; used for routing, config, and registry lookup.
- **`output_model`** — the Pydantic class returned by every action (Drive uses one shared envelope with `raw` + `description`).
- **`error_map`** — maps exception types to `(ErrorCategory, error_code)`. Entries are registered with `ErrorMapper` automatically at class definition time.
- **`build_client()`** — override to create the Google API client. `get_client()` caches the result in `self._client`.
- **`action_specs`** — each key becomes a manifest action (e.g. `files.list`). Do **not** also add a manual `@nw_action` with the same name.
- **`_execute_action_spec`** — **required** when using **`action_specs`**: each generated handler delegates here. Typically call **`execute_spec_in_thread`** for blocking SDKs (such as `googleapiclient`). Connectors that only use hand-written `@nw_action` methods do not implement this hook.

**Adding a new Drive operation:** add a Pydantic variant and extend the union in `schema.py`, register a new `SdkActionSpec` in `action_spec.py`, and rely on auto-generated handlers (see [`src/node_wire_google_drive/README.md`](../src/node_wire_google_drive/README.md)).

### Step 4 — Register in `config/connectors.yaml`

```yaml
connectors:
  google_drive:
    enabled: true
    exposed_via:
      - rest
      - grpc
      - mcp
```

`exposed_via` controls which bindings surface the connector. Use any subset of **`rest`**, **`grpc`**, and **`mcp`** (omit protocols you do not need).

### Step 5 — Auto-registration (nothing extra needed)

`BaseConnector.__init_subclass__` adds your class to `_CONNECTOR_REGISTRY[connector_id]` as soon as `logic.py` is imported. **`node_wire_runtime.connector_registry.auto_register()`** performs those imports at startup. **No manual factory branch is required.**

---

## Single-action connector example

A connector with one action is identical in structure — just add one `@nw_action` method:

```python
# src/node_wire_sms/schema.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel

class SmsSendInput(BaseModel):
    action: Literal["send"] = "send"
    to: str
    message: str

class SmsSendOutput(BaseModel):
    message_sid: str
    status: str
```

```python
# src/node_wire_sms/logic.py
from __future__ import annotations

from node_wire_runtime import BaseConnector, nw_action
from .schema import SmsSendInput, SmsSendOutput


class SmsConnector(BaseConnector):
    connector_id = "sms"
    output_model = SmsSendOutput

    @nw_action("send")
    async def send(self, params: SmsSendInput, *, trace_id: str) -> SmsSendOutput:
        api_key = self.secret_provider.get_secret("sms_api_key")
        # ... call SMS vendor API ...
        return SmsSendOutput(message_sid="SM123", status="queued")
```

---

## Calling a connector directly (in-process)

Use `connector.run(dict)` for the full pipeline (validation, policy, retries, error mapping):

```python
from node_wire_runtime.connector_registry import auto_register
from bindings.factory import ConnectorFactory

auto_register()
factory = ConnectorFactory()
factory.load()

connector = factory.get_for_protocol("google_drive", "rest", action="files.list")
response = await connector.run(
    {"action": "files.list", "page_size": 10, "query": "mimeType = 'application/vnd.google-apps.folder'"}
)

if response.success:
    print(response.data)   # {"raw": {"files": [...], ...}, "description": "Successfully executed files.list"}
else:
    print(response.error_code, response.message)
```

For composing actions within a connector, use **`self.call_action`** (returns the action’s output model, not `ConnectorResponse`):

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

## Integrating with binding layers

The factory and manifest drive all bindings. Once a connector is registered and `load()` is called, REST, gRPC, and MCP discover enabled connectors according to `exposed_via`.

### Optional: MCP under `src/agents/` (ToolHive / stdio)

The repo also ships **stdio MCP servers** for agents and ToolHive under `src/agents/` (e.g. `python -m agents.mcp_entrypoint`, per-connector modules). Those are separate from `MODE=MCP` on `node-wire`; see **[mcp-servers.md](mcp-servers.md)** for images, env, and registration. Wiring a connector in `config/connectors.yaml` does not by itself add a ToolHive image — follow **mcp-servers.md** when you need a dedicated MCP deployment.

### REST binding

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

### MCP binding

`src/bindings/mcp_server/server.py` registers one **MCP tool** per manifest entry. Tool names follow the pattern `{connector_id}.{action}` (e.g. `google_drive.files.list`, `google_drive.files.upload`).

The MCP server calls `connector.run(args_dict)` and serialises the `ConnectorResponse` as the tool result.

The **tool name** (`<connector_id>.<action>`) is authoritative: after normalizers run, the binding sets `action` from the tool name. A conflicting `action` in the payload is rejected (see `enforce_authoritative_action` in `src/node_wire_runtime/ingress.py`).

Optional per-action **argument normalizers** (`mcp_normalize` on `@sdk_action` / `SdkActionSpec`) run before `connector.run` to map LLM aliases to canonical fields. Actions default to **strict** JSON Schema (`additionalProperties: false`); set `alias_tolerant=True` only where extra keys must pass MCP SDK validation before normalization.

Published **`input_schema` omits the `action` property** (manifest contract v2+): clients must not rely on sending `action` inside tool arguments; the MCP tool name (or REST path) is authoritative.

**FHIR `search_encounter` (Epic/Cerner):** normalizers map root-level `patient` / `patientId` to `patient_id`, and `sort` → `_sort` (via `search_params`). Encounter search **requires** a patient filter (`patient_id` or `patient` in `search_params`) before any outbound FHIR call.

### Manifest

`build_manifest(connectors)` is the single source of truth for both bindings (by default it strips `action` from each entry’s `input_schema`). It returns one entry per `@sdk_action`:

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

## Connector inventory

| Connector | Primary actions |
|-----------|-----------------|
| `http_generic` | `request` |
| `smtp` | `send_email` |
| `stripe` | `charge` |
| `salesforce` | `create_lead`, `read_lead`, `update_lead`, `delete_lead`, `create_contact`, `read_contact`, `update_contact`, `delete_contact` |
| `google_drive` | `files.list`, `files.upload`, … (see `action_specs`) |

| `fhir_epic` | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` |
| `fhir_cerner` | Same family as Epic with Cerner-specific schemas |
| `slack` | `post_message`, `send_direct_message`, `upload_file` |

MCP tool names: **`<connector_id>.<action>`** (e.g. `fhir_epic.read_patient`). See [`docs/mcp-servers.md`](mcp-servers.md).

---

## Adding a new connector (checklist)

3. In `logic.py`: subclass `BaseConnector`, set `connector_id` and `output_model`, then add `@nw_action` methods or wire `action_specs`. 
4. **Authentication**: Delegate all header construction to **`self.get_auth_headers()`**. Do not hardcode secret lookups or IdP handshakes and ensure sensitive fields are removed from your `input_schema`.
5. For SDK-style connectors, add an `action_spec.py` (or similar) with `SdkActionSpec` entries and use **`execute_spec_in_thread`** when the vendor client is blocking.
6. Optionally add `error_map` and/or `registration.py` for custom exception handling.
7. Add the connector to **`config/connectors.yaml`** with `enabled: true`, the desired `exposed_via` protocols, and an **`auth:`** block.
8. That's it — `auto_register()` handles the rest. No factory branch required.

---

## Configuration reference

### `config/connectors.yaml`

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

### `ConnectorFactory` API

| Method | Description |
|--------|-------------|
| `load()` | Reads YAML, instantiates all enabled connectors from `_CONNECTOR_REGISTRY`. |
| `get_for_protocol(id, protocol, action=None)` | Returns connector if enabled and exposed for that protocol; `None` otherwise. |
| `list_for_protocol(protocol)` | All connectors exposed for a given protocol. |

---

## Security (REST, plugins, secrets)

**REST API (`bindings.rest_api`)** — `GET /health` is unauthenticated. All other routes (`/connectors/...`, `/playground/...`, `/scenarios/...`, OpenAPI) require **`NW_REST_API_KEY`** via `Authorization: Bearer <key>` or `X-API-Key: <key>`, optional **`NW_REST_JWT_SECRET`** for HS256 JWTs. Set **`NW_REST_AUTH_DISABLED=true`** only for local development. Production: set **`NW_REST_LOAD_DOTENV=false`** so secrets are not read from a `.env` file on disk.

**Connector entry points** — Any installed distribution may register `node_wire.connectors`. For production, set **`NW_ALLOWED_CONNECTORS`** to a comma-separated list of entry point names (e.g. `fhir_epic,http_generic`). **`NW_CONNECTOR_MODULE_PREFIX`** defaults to `node_wire_`; modules not under that prefix are skipped.

**Secrets** — `EnvSecretProvider` looks up the key **as given**, then **`key.upper()`** (e.g. `my_key` then `MY_KEY`). It raises **`SecretNotFoundError`** when a variable is missing (fail-closed). Set **`NW_ENV_SECRET_LEGACY_EMPTY=true`** only if you need legacy empty-string behaviour. **`NW_SECRET_BACKEND=aws_env`** with **`NW_AWS_SECRETS_MANAGER_SECRET_ID`** composes AWS Secrets Manager JSON + env fallback via `ChainedSecretProvider` (see `bindings.factory._build_secret_provider`).

---

## Related documentation

- [packaging.md](packaging.md) — Wheel build lifecycle, PyPI publish flow, client install model, secrets config, and pre-publish checklist.
- [mcp-servers.md](mcp-servers.md) — MCP images, ToolHive, env vars.
- [google_drive_connector.md](google_drive_connector.md) — Drive REST API and setup.
- [salesforce_connector.md](salesforce_connector.md) — Salesforce CRM operations and playground.
- [slack_connector.md](slack_connector.md) — Slack bot token and setup.
- Per-connector READMEs under `src/node_wire_*/README.md` where present.

