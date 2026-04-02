# Connectors guide (`src/connectors`)

This guide explains how **connectors** fit into Node Wire, how to build your own connector, and how the runtime and bindings wire everything together. Connector implementations live under `src/connectors/`; the shared base class lives at **`src/runtime/base_connector.py`**.

---

## Package layout and registration

Each connector is a **subpackage** of `connectors`:

| File | Role |
|------|------|
| `schema.py` | Pydantic input/output models. Each input model has an `action: Literal[...]` discriminator field. |
| `logic.py` | Connector class: `BaseConnector` subclass with `@sdk_action` methods. |
| `registration.py` | Optional: registers connector-specific exceptions with `ErrorMapper`. |
| `exceptions.py` | Optional: custom exception types. |

At startup, call **`connectors.auto_register()`** (see [`src/connectors/__init__.py`](../src/connectors/__init__.py)): it imports each subpackage's `logic` module, which triggers `BaseConnector.__init_subclass__` and adds the class to `_CONNECTOR_REGISTRY`. Any `registration.py` is imported afterwards.

---

## The unified `BaseConnector`

There is one base class for all connectors: **`BaseConnector`** (`src/runtime/base_connector.py`). It handles:

- Input validation via a Pydantic **discriminated union** (the `action` field selects the right model)
- Optional **policy hook** enforcement
- **Retries and circuit breaking** via `with_resilience`
- **Error mapping** via `ErrorMapper`
- OpenTelemetry **tracing**
- A standard **`ConnectorResponse`** envelope

Actions are declared with the **`@sdk_action("name")`** decorator on async methods. A connector can have **one or many** actions — there is no separate "single-action" type.

```
flowchart LR
  yaml[connectors.yaml]
  factory[ConnectorFactory.load]
  inst[BaseConnector subclass]
  run[connector.run]
  exec[internal_execute → @sdk_action dispatch]
  resp[ConnectorResponse]
  yaml --> factory --> inst --> run --> exec --> resp
```

---

## Building a custom connector

### Step 1 — Define your schemas (`schema.py`)

Each action needs an **input model** and an **output model**. Input models must include an `action` field with a `Literal` type so the runtime can dispatch to the right handler.

```python
# src/connectors/weather/schema.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class CurrentWeatherInput(BaseModel):
    action: Literal["current_weather"] = "current_weather"
    city: str
    units: Literal["metric", "imperial"] = "metric"


class ForecastInput(BaseModel):
    action: Literal["forecast"] = "forecast"
    city: str
    days: int = 5


class WeatherOutput(BaseModel):
    city: str
    temperature: float
    description: str
```

When a connector only has **one** action, the `action` field is still required — the runtime always validates through the discriminated union.

### Step 2 — Implement the connector class (`logic.py`)

Subclass `BaseConnector`, set `connector_id` and `output_model`, and annotate each action method with `@sdk_action`. The method signature must be fully type-annotated.

```python
# src/connectors/weather/logic.py
from __future__ import annotations

import httpx
import logging

from runtime import BaseConnector, sdk_action
from runtime.models import ErrorCategory

from .schema import CurrentWeatherInput, ForecastInput, WeatherOutput

logger = logging.getLogger("connectors.weather")


class WeatherConnector(BaseConnector):
    """Weather API connector — demonstrates a two-action connector."""

    connector_id = "weather"
    output_model = WeatherOutput

    error_map = {
        httpx.HTTPStatusError: (ErrorCategory.RETRYABLE, "WEATHER_HTTP_ERROR"),
        httpx.TimeoutException:  (ErrorCategory.RETRYABLE, "WEATHER_TIMEOUT"),
    }

    def build_client(self) -> httpx.AsyncClient:
        """Called once on first use; result cached in self._client."""
        api_key = self.secret_provider.get_secret("weather_api_key")
        return httpx.AsyncClient(
            base_url="https://api.openweathermap.org/data/2.5",
            params={"appid": api_key},
            timeout=10.0,
        )

    @sdk_action("current_weather")
    async def current_weather(
        self, params: CurrentWeatherInput, *, trace_id: str
    ) -> WeatherOutput:
        client: httpx.AsyncClient = self.get_client()
        resp = await client.get(
            "/weather",
            params={"q": params.city, "units": params.units},
        )
        resp.raise_for_status()
        data = resp.json()
        return WeatherOutput(
            city=data["name"],
            temperature=data["main"]["temp"],
            description=data["weather"][0]["description"],
        )

    @sdk_action("forecast")
    async def forecast(
        self, params: ForecastInput, *, trace_id: str
    ) -> WeatherOutput:
        client: httpx.AsyncClient = self.get_client()
        resp = await client.get(
            "/forecast",
            params={"q": params.city, "cnt": params.days},
        )
        resp.raise_for_status()
        data = resp.json()
        first = data["list"][0]
        return WeatherOutput(
            city=data["city"]["name"],
            temperature=first["main"]["temp"],
            description=first["weather"][0]["description"],
        )
```

Key points:
- **`connector_id`** — unique string; used for routing, config, and registry lookup.
- **`output_model`** — the Pydantic class returned by every action.
- **`error_map`** — maps exception types to `(ErrorCategory, error_code)`. Entries are registered with `ErrorMapper` automatically at class definition time.
- **`build_client()`** — override to create a vendor SDK client. `get_client()` caches the result in `self._client`.
- **`@sdk_action("name")`** — marks an async method as a named action. `name` becomes the routing key and the manifest action identifier.

### Step 3 — Register in `config/connectors.yaml`

```yaml
connectors:
  weather:
    enabled: true
    exposed_via:
      - rest
      - mcp
```

`exposed_via` controls which bindings surface the connector. Available values: `rest`, `mcp`.

### Step 4 — Auto-registration (nothing extra needed)

`BaseConnector.__init_subclass__` adds your class to `_CONNECTOR_REGISTRY[connector_id]` as soon as `logic.py` is imported. `connectors.auto_register()` handles that import at startup. **No manual factory branch is required.**

---

## Single-action connector example

A connector with one action is identical in structure — just add one `@sdk_action` method:

```python
# src/connectors/sms/schema.py
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
# src/connectors/sms/logic.py
from __future__ import annotations

from runtime import BaseConnector, sdk_action
from .schema import SmsSendInput, SmsSendOutput


class SmsConnector(BaseConnector):
    connector_id = "sms"
    output_model = SmsSendOutput

    @sdk_action("send")
    async def send(self, params: SmsSendInput, *, trace_id: str) -> SmsSendOutput:
        api_key = self.secret_provider.get_secret("sms_api_key")
        # ... call SMS vendor API ...
        return SmsSendOutput(message_sid="SM123", status="queued")
```

---

## Generated actions with `action_specs`

For connectors where actions share a common execution pattern (e.g. wrapping a REST API with many endpoints), use **`action_specs`** to generate `@sdk_action` methods automatically. Each `SdkActionSpec` entry produces a handler that calls `self._execute_action_spec(action_name, params, ...)`.

```python
from runtime import BaseConnector, SdkActionSpec
from .schema import FilesListInput, FilesUploadInput, DriveOutput

class GoogleDriveConnector(BaseConnector):
    connector_id = "google_drive"
    output_model = DriveOutput

    action_specs = {
        "files.list":   SdkActionSpec(input_model=FilesListInput,   output_model=DriveOutput),
        "files.upload": SdkActionSpec(input_model=FilesUploadInput, output_model=DriveOutput),
    }
```

`action_specs` is processed before `@sdk_action` discovery, so the generated methods participate in the same discriminated-union validation and manifest generation.

---

## Calling a connector directly (in-process)

Use `connector.run(dict)` for the full pipeline (validation, policy, retries, error mapping):

```python
from connectors import auto_register
from bindings.factory import ConnectorFactory

auto_register()
factory = ConnectorFactory()
factory.load()

connector = factory.get_for_protocol("weather", "rest", action="current_weather")
response = await connector.run({"action": "current_weather", "city": "London"})

if response.success:
    print(response.data)   # {"city": "London", "temperature": 15.3, "description": "light rain"}
else:
    print(response.error_code, response.message)
```

For composing actions within a connector, use **`self.call_action`**:

```python
@sdk_action("summary")
async def summary(self, params: SummaryInput, *, trace_id: str) -> WeatherOutput:
    # Internally invoke another action on the same connector
    return await self.call_action("current_weather", {"city": params.city})
```

---

## Integrating with binding layers

The factory and manifest drive all bindings. Once a connector is registered and `load()` is called, every binding (REST, MCP) discovers it automatically.

### REST binding

`src/bindings/rest_api/app.py` calls `build_manifest(connectors)` and registers a `POST /connectors/{connector_id}/{action}` route for every manifest entry:

```
POST /connectors/weather/current_weather
Content-Type: application/json

{ "city": "Tokyo", "units": "metric" }
```

The `action` field in the body is optional for REST — the binding injects it from the URL path. The runtime then performs full Pydantic validation and returns a `ConnectorResponse`.

**Response envelope:**

```json
{
  "success": true,
  "data": { "city": "Tokyo", "temperature": 22.1, "description": "clear sky" },
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

`src/bindings/mcp_server/server.py` registers one **MCP tool** per manifest entry. Tool names follow the pattern `{connector_id}.{action}` (e.g. `weather.current_weather`, `google_drive.files.upload`).

The MCP server calls `connector.run(args_dict)` and serialises the `ConnectorResponse` as the tool result.

### Manifest

`build_manifest(connectors)` is the single source of truth for both bindings. It returns one entry per `@sdk_action`:

```python
[
  {
    "connector_id": "weather",
    "action": "current_weather",
    "input_schema": { ... },   # JSON Schema from CurrentWeatherInput
    "output_schema": { ... },  # JSON Schema from WeatherOutput
  },
  {
    "connector_id": "weather",
    "action": "forecast",
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
| `google_drive` | `files.list`, `files.upload`, … (see `action_specs`) |
| `fhir_epic` | `read_patient`, `search_patients`, `search_encounter`, `create_document_reference`, `search_document_reference` |
| `fhir_cerner` | Same family as Epic with Cerner-specific schemas |

MCP tool names: **`<connector_id>.<action>`** (e.g. `fhir_epic.read_patient`). See [`docs/mcp-servers.md`](mcp-servers.md).

---

## Adding a new connector (checklist)

1. Create `src/connectors/<connector_id>/` with `schema.py` and `logic.py`.
2. In `schema.py`: define one Pydantic input model per action, each with `action: Literal["<name>"]`, and one or more output models.
3. In `logic.py`: subclass `BaseConnector`, set `connector_id` and `output_model`, add `@sdk_action` methods with full type annotations.
4. Optionally add `error_map` and/or `registration.py` for custom exception handling.
5. Add the connector to **`config/connectors.yaml`** with `enabled: true` and the desired `exposed_via` protocols.
6. That's it — `auto_register()` handles the rest. No factory branch required.

---

## Configuration reference

### `config/connectors.yaml`

```yaml
connectors:
  <connector_id>:
    enabled: true          # false → connector not instantiated
    exposed_via:           # controls which bindings surface this connector
      - rest
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

## Related documentation

- [mcp-servers.md](mcp-servers.md) — MCP images, ToolHive, env vars.
- [google_drive_connector.md](google_drive_connector.md) — Drive REST API and setup.
- Per-connector READMEs under `src/connectors/*/README.md` where present.
