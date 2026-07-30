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
| `__init__.py` | Required empty file — marks the directory as a Python package. |
| `schema.py` | Pydantic input/output models. Each input model has an `action: Literal[...]` discriminator field (often combined into a discriminated union). |
| `logic.py` | Connector class: `BaseConnector` subclass — either explicit `@nw_action` methods, or **`action_specs`** plus an optional `_execute_action_spec` override for SDK dispatch. |
| `action_spec.py` (optional) | Declarative `SdkActionSpec` entries mapping validated models to vendor SDK calls (see Google Drive). |
| `registration.py` | Optional: registers connector-specific exceptions with `ErrorMapper`. |
| `exceptions.py` | Optional: custom exception types. |

At startup, call **`node_wire_runtime.connector_registry.auto_register()`**: it loads entry points in group `node_wire.connectors`, imports each connector's `logic` module (triggering `BaseConnector.__init_subclass__`, which populates the registry returned by `get_connector_registry()`), then imports optional `registration.py` for `ErrorMapper` side effects.

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
    raw: dict | list
    description: str
```

Use `dict | list` for `raw` when vendor APIs return arrays (e.g. list endpoints); Pydantic validates either shape. Per-action output models can use typed fields instead of a shared envelope.

When a connector only has **one** action, the `action` field is still required — the runtime always validates through the discriminated union.

### Step 2 — Map operations to the SDK (`action_spec.py`)

**`SdkActionSpec`** describes how to turn a validated model into a single SDK call: resource path (`resource_segments`), HTTP-style method name (`method_name`), and how to build `body` / keyword arguments from the model. The full Drive registry lives in [`src/node_wire_google_drive/action_spec.py`](https://github.com/AOT-Technologies/node-wire/blob/main/src/node_wire_google_drive/action_spec.py).

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
```

## Connector Authentication

Node Wire provides a shared **`AuthProvider`** abstraction (`src/node_wire_runtime/auth/`) that handles token acquisition, JWT construction (for SMART on FHIR), caching, and expiry. This ensures that connector logic (`logic.py`) does not need to handle raw credentials or IdP-specific handshake details.

### Using Auth in a Connector

To use authentication, call **`await self.get_auth_headers()`** (inherited from `BaseConnector`). This returns a dictionary of headers (e.g. `{"Authorization": "Bearer <token>"}`) injected by the configured provider.

There are two patterns depending on how your connector talks to the vendor:

**HTTP connectors** (direct REST calls via `httpx`) — create a short-lived client inside each `@nw_action` method. Do **not** override `build_client()`:

```python
# logic.py — HTTP connector pattern (e.g. Slack, FHIR, GitHub)
# Base URL: read from the connector's own config, an env var, or a module constant.
# There is no inherited _get_base_url() helper — connectors own their URL resolution.
BASE_URL = "https://api.example.com"   # or: os.environ["MY_SERVICE_URL"]

@nw_action("read_resource")
async def read_resource(self, params: In, *, trace_id: str) -> Out:
    headers = await self.get_auth_headers()  # Fetched/cached by provider
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/resource", headers=headers)
        resp.raise_for_status()
    ...
```

**SDK connectors** (vendor Python SDK with a long-lived client object) — override `build_client()` so `get_client()` can cache the result across calls. Auth is handled inside `build_client()`, not via `get_auth_headers()`:

```python
# logic.py — SDK connector pattern (e.g. Google Drive)
def build_client(self) -> Any:
    # Read credential from secret provider and build the vendor client once.
    raw_sa = self.secret_provider.get_secret("MY_SA_JSON")
    creds = ...
    return vendor_sdk.build("v1", credentials=creds)

async def _execute_action_spec(self, action_name, params, *, trace_id, log_extra=None):
    client = self.get_client()  # cached; calls build_client() on first use
    ...
```

### Supported Provider Types

Choose a provider in your **`connectors.yaml`** via the `auth:` block:

| Type | Description | Example connector |
|------|-------------|-------------------|
| **`none`** | (Default) No auth headers added. | `http_generic` |
| **`static_token`** | Uses a fixed token from a secret (Bearer, Basic, or custom). Supports refresh. | `stripe`, `slack` |
| **`static_credentials`** | Username + password pair (e.g. SMTP relay). | `smtp` |
| **`service_account`** | Google-style service account JSON + scopes. | `google_drive` |
| **`oauth2`** | Token exchange (`private_key_jwt`, `refresh_token`, `client_secret_post`, etc.). Handles caching and expiry. | `fhir_epic`, `salesforce` |

#### `static_token` field reference

| Field | Required | Default | Notes |
|-------|----------|---------|-------|
| `secret_key` | Yes | — | Env var name holding the raw token value (`EnvSecretProvider` tries the key as-is, then uppercased). |
| `header_name` | No | `Authorization` | HTTP header the token is injected into. |
| `prefix` | No | `Bearer ` (with trailing space) | String prepended to the token value. Set `prefix: ""` for APIs that expect the raw token (e.g. Stripe). Set `prefix: "token "` for APIs that require the `token` scheme (check your vendor's auth docs). |

So `slack` (no `header_name`/`prefix`) produces `Authorization: Bearer <SLACK_BOT_TOKEN>`, and `stripe` (with `prefix: ""`) produces `Authorization: <STRIPE_API_KEY>`.

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
      secret_key: stripe_api_key
      header_name: Authorization
      prefix: ""  # Stripe expects raw key; env var is STRIPE_API_KEY

  smtp:
    enabled: true
    auth:
      provider: static_credentials
      username_secret: SMTP_USERNAME
      password_secret: SMTP_PASSWORD

  google_drive:
    enabled: true
    auth:
      provider: service_account
      sa_json_secret: GOOGLE_DRIVE_SA_JSON
      scopes:
        - https://www.googleapis.com/auth/drive
```

---

Key points:
- **`connector_id`** — unique string; used for routing, config, and registry lookup.
- **`output_model`** — the Pydantic class returned by every action. Shared envelopes often use `raw: dict | list` for list-heavy vendor APIs; per-action models can use typed fields instead (see SMS example below).
- **`error_map`** — maps exception types to `(ErrorCategory, error_code)`. Entries are registered with `ErrorMapper` automatically at class definition time.
- **`build_client()`** — override to create the Google API client. `get_client()` caches the result in `self._client`.
- **`action_specs`** — each key becomes a manifest action (e.g. `files.list`). Do **not** also add a manual `@nw_action` with the same name.
- **`_execute_action_spec`** — **required** when using **`action_specs`**: each generated handler delegates here. Typically call **`execute_spec_in_thread`** for blocking SDKs (such as `googleapiclient`). Connectors that only use hand-written `@nw_action` methods do not implement this hook.

**Adding a new Drive operation:** add a Pydantic variant and extend the union in `schema.py`, register a new `SdkActionSpec` in `action_spec.py`, and rely on auto-generated handlers (see [`src/node_wire_google_drive/README.md`](https://github.com/AOT-Technologies/node-wire/blob/main/src/node_wire_google_drive/README.md)).

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

`BaseConnector.__init_subclass__` registers your class in the global registry as soon as `logic.py` is imported. **`node_wire_runtime.connector_registry.auto_register()`** performs those imports at startup. **No manual factory branch is required.**

### Connector registry API

`get_connector_registry()` is defined in `base_connector.py` and exported from the top-level `node_wire_runtime` package — it is **not** in `node_wire_runtime.connector_registry`. Use it to read the connector-id → class map after `auto_register()` has imported your `logic` module:

```python
from node_wire_runtime import get_connector_registry
from node_wire_runtime.connector_registry import auto_register

auto_register()  # requires NW_ALLOWED_CONNECTORS
registry = get_connector_registry()  # Dict[str, Type[BaseConnector]]
connector_cls = registry["google_drive"]
```

For the full run pipeline (YAML config, instantiation, protocol routing), use **`ConnectorFactory`** (see [Calling a connector directly](#calling-a-connector-directly-in-process)).

### Optional: `registration.py` for ErrorMapper

When exceptions are raised outside the connector class (or shared across modules), register them in `registration.py` instead of inline `error_map`:

```python
# src/node_wire_<name>/registration.py
from node_wire_runtime import ErrorCategory, ErrorMapper

from .exceptions import MyAuthError, MyRateLimitError

ErrorMapper.register(MyAuthError, ErrorCategory.AUTH, code="MY_AUTH_ERROR")
ErrorMapper.register(MyRateLimitError, ErrorCategory.RETRYABLE, code="MY_RATE_LIMIT")
```

`auto_register()` imports `registration.py` after `logic.py`, so these registrations run at startup. Alternatively, use inline **`error_map`** on the connector class (Google Drive example above).

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

Use `connector.run(dict)` for the full pipeline (validation, policy, retries, error mapping).

Set **`NW_ALLOWED_CONNECTORS`** to a comma-separated list of entry-point names (e.g. `google_drive`) before calling `auto_register()` — without it, `auto_register()` loads nothing (fail-closed).

```python
import os

from node_wire_runtime.connector_registry import auto_register
from bindings.factory import ConnectorFactory

os.environ["NW_ALLOWED_CONNECTORS"] = "google_drive"
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

For composing actions within a connector, use **`self.call_action`**. It routes through **`connector.run`** so **policy hooks**, **resilience**, and the **`ConnectorResponse`** error path apply (including MCP scope policy). It returns the nested action’s **output model** on success (validated from `run()`’s `data`). On policy denial it raises **`PolicyDenied`**, which the outer `run()` maps like any other action failure.

Optional keyword args `principal`, `tenant_id`, and `scopes` override the caller identity for the nested call. When omitted, **`call_action` inherits** identity from the outer `run()` (MCP/REST with JWT or scoped API key), so nested actions receive the same authorization as a direct tool call.

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

### Runtime (dev)

1. Create the package directory `src/node_wire_<name>/`. The directory **must contain `__init__.py`** (empty is fine) to be importable as a Python package. Add `schema.py` with Pydantic input/output models and register the entry point under `[project.entry-points."node_wire.connectors"]` in the root `pyproject.toml`.
2. In `logic.py`: subclass `BaseConnector`, set `connector_id` and `output_model`, then add `@nw_action` methods or wire `action_specs`. If your connector makes outbound HTTP calls (e.g. using `httpx`), declare that library as a dependency in the connector's `packages/connectors/<name>/pyproject.toml`. For HTTP-based connectors use an inline `async with httpx.AsyncClient() as client:` inside each `@nw_action` method (see [Using Auth in a Connector](#using-auth-in-a-connector)); only override `build_client()` / `get_client()` when wrapping a vendor SDK that requires a long-lived client object (e.g. `google_drive`).
3. **Authentication**: Delegate all header construction to **`self.get_auth_headers()`**. Do not hardcode secret lookups or IdP handshakes and ensure sensitive fields are removed from your `input_schema`.
4. For SDK-style connectors, add an `action_spec.py` (or similar) with `SdkActionSpec` entries and use **`execute_spec_in_thread`** when the vendor client is blocking.
5. Optionally add `error_map` and/or `registration.py` for custom exception handling (see [registration.py example](#optional-registrationpy-for-errormapper) below).
6. Add the connector to **`config/connectors.yaml`** with `enabled: true`, the desired `exposed_via` protocols, and an **`auth:`** block.
7. **Environment template:** Add required secrets and connector-specific vars to [`sample.env`](https://github.com/AOT-Technologies/node-wire/blob/main/sample.env) (referenced by [configuration.md](configuration.md) and [installation.md](installation.md)). Use commented placeholders with the env var names your connector reads via `SecretProvider`. Also add the new connector's entry-point name to the `NW_ALLOWED_CONNECTORS` line so the template stays current.
8. `auto_register()` handles runtime registration — **no factory branch required**.

### Publishable PyPI package (when shipping on PyPI)

9. Create `packages/connectors/<name>/pyproject.toml` and `packages/connectors/<name>/setup.py`. See [packaging.md — Tier 2 templates](packaging.md#tier-2-templates) for copy-paste starting points for both files.
10. Add the package path to **`scripts/build-packages.sh`** (`ALL_PACKAGES`) and to the three CI workflow allowlists — see [packaging.md — CI allowlist updates](packaging.md#ci-allowlist-updates) for the exact lines to add in each file.
11. Update the inventory table in **[packaging.md](packaging.md)**.

### Standalone MCP server (optional — dedicated Docker/ToolHive image)

> **Prerequisite:** Complete Steps 9–11 (Tier 2) first. The Dockerfile copies pre-built `.whl` files from `packages/connectors/<name>/dist/`; that directory does not exist until you run `bash scripts/build-packages.sh packages/connectors/<name>`.

12. Add `src/agents/<name>_mcp.py`, a `[project.scripts]` entry in root `pyproject.toml`, `docker/<name>/Dockerfile`, and entries in **`scripts/build-mcp-images.sh`**, **`docker-compose.mcp.yml`**, and **[local-packages-to-images.md](local-packages-to-images.md)** (wheel → image mapping table).
13. Add a row to the naming table in **[mcp-servers.md](mcp-servers.md)** and update the architecture diagram in that file to include the new connector.

For full file lists see [packaging.md — Adding a new publishable connector](packaging.md#adding-a-new-publishable-connector).

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
| `load()` | Reads YAML, instantiates all enabled connectors from the connector registry (`get_connector_registry()`). |
| `get_for_protocol(id, protocol, action=None)` | Returns connector if enabled and exposed for that protocol; `None` otherwise. |
| `list_for_protocol(protocol)` | All connectors exposed for a given protocol. |

---

## Security (REST, plugins, secrets)

**MCP (`bindings.mcp_server`)** — Configure **`NW_MCP_API_KEY_SCOPES`** (and optionally **`NW_MCP_ACTION_SCOPE_MAP_JSON`**) so `tools/list` and `tools/call` align with the same scope rules. Code default is **`NW_MCP_SCOPE_POLICY_DEFAULT=deny`** when unset. Optional guardrail **`NW_MCP_SCOPE_POLICY_STRICT=true`** fails startup when scope policy would otherwise be effectively disabled (explicit `allow` + empty map). API key wildcard (`"*"`) is explicit and intentionally bypasses per-action scope restrictions; use only for deliberate super-user keys. JWTs use claim `scopes` / `scope` and must include **`exp`**, **`iat`**, **`aud`** (match **`NW_JWT_AUDIENCE`**), and **`iss`** (match **`NW_JWT_ISSUER`**) when **`NW_MCP_JWT_SECRET`** is set.

**gRPC (`bindings.grpc_server`)** — Configure **`NW_GRPC_API_KEY_SCOPES`** (and optionally **`NW_MCP_ACTION_SCOPE_MAP_JSON`**) so authenticated gRPC calls use the same scope rules as MCP/REST. Caller identity is propagated from the auth interceptor into `connector.run`.

**REST API (`bindings.rest_api`)** — `GET /health` is unauthenticated. All other routes (`/connectors/...`, `/playground/...`, `/scenarios/...`, OpenAPI) require **`NW_REST_API_KEY`** via `Authorization: Bearer <key>` or `X-API-Key: <key>`, optional **`NW_REST_JWT_SECRET`** for HS256 JWTs (with **`NW_JWT_AUDIENCE`** / **`NW_JWT_ISSUER`** and required `exp`/`iat`/`aud`/`iss` claims). API key scopes use **`NW_REST_API_KEY_SCOPES`** (same format as MCP). Set **`NW_REST_AUTH_DISABLED=true`** only for local development. Production: set **`NW_REST_LOAD_DOTENV=false`** so secrets are not read from a `.env` file on disk.

**HTTP Generic outbound policy** — `http_generic.request` allows only `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, and input methods are normalized to uppercase before validation. URLs targeting internal destinations are rejected (`localhost`, loopback, private/link-local IP ranges, metadata endpoints). Connector logs sanitize URL fields by dropping query strings and fragments so only scheme/host/path are retained.

**SMTP outbound policy** — `smtp.send_email` accepts only message fields (`to`, `subject`, `body`, optional `from_email`). SMTP relay settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`) are server-side only and cannot be set in the request payload. Credentials are never sent to a caller-chosen host. For production, optionally set **`NW_SMTP_ALLOWED_HOSTS`** to a comma-separated list of permitted relay hostnames. The `subject` field must not contain newline or ASCII control characters (header-injection defense); `body` may be multiline.

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

