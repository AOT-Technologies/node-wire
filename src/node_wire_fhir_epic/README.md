<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# FHIR Epic connector — technical documentation

> **Platform:** Node Wire
> **Standard:** FHIR R4
> **Auth method:** SMART Backend Services — RS384 JWT / OAuth2
> **Actions:** `read_patient` · `search_patients` · `search_encounter` · `create_document_reference` · `search_document_reference`
> **Source:** `src/node_wire_fhir_epic/`

---

## Architecture overview

The FHIR Epic connector is designed to interface with Epic EHR systems using the FHIR R4 standard. Unlike simpler connectors, this connector is **multi-action**, meaning a single configuration entry in `connectors.yaml` exposes multiple distinct operations (actions).

### Logic consolidation

All actions live in a single **"Fat Connector"** class rather than one class per action:

- **`FhirEpicConnector`**: A single `BaseConnector` subclass that encapsulates all shared logic, authentication flows (JWT/OAuth2), and the per-action implementation methods. Each action is a method decorated with **`@sdk_action`** or **`@nw_action`** (for example, `read_patient`, `search_patients`, `search_encounter`). The base class derives routing and manifest entries from that decorator metadata — there is no separate per-action wrapper class.

---

## Framework integration and decisions

This section records how the platform's core bindings support the consolidated architecture.

### `factory.py` — multi-action support

The `ConnectorFactory` was updated to support connectors that manage many actions internally.

- **Design Decision**: Instead of the factory returning a list of different connector instances for `fhir_epic`, it returns **one instance** of the `FhirEpicConnector` class. This keeps the `_connectors` dictionary clean (one entry per `connector_id`).
- **Action Discovery**: The runtime discovers actions from the `@sdk_action`/`@nw_action` metadata on the connector (`sdk_action_metas()` → `build_manifest` in `node_wire_runtime/manifest.py`), emitting one manifest entry and one REST route per action.

### `app.py` — dynamic REST routes

`_build_dynamic_routes()` in `src/bindings/rest_api/app.py` registers one `POST /connectors/{connector_id}/{action}` route per manifest entry.

- **The design**: Each endpoint accepts an arbitrary JSON object (`payload: Dict[str, Any]`) and forwards it to `connector.run(...)`. FastAPI's type introspection cannot reliably resolve a different Pydantic model per route when routes are generated in a loop, which previously caused `422` errors.
- **Validation**: The runtime performs the full Pydantic validation of the payload inside `connector.run()`.

### Runtime logging — avoiding collisions

The runtime (`base_connector.py` and `resilience.py`) uses `error_message` instead of `message` for the exception-detail log attribute.

- **The problem**: The Python `logging` module uses `message` as a reserved internal attribute on `LogRecord` objects. Overwriting it in the `extra` dictionary can lead to lost data or crashes in some logging formatters.
- **The solution**: Using `error_message` ensures that the technical details of the exception are preserved and distinctly visible in structured logging backends (like OpenTelemetry) without conflicting with the standard log event description.

### `config/connectors.yaml` — connector configuration

`config/connectors.yaml` contains a `fhir_epic` entry.

- **Why**: This allows the `ConnectorFactory` to recognize and instantiate the FHIR connector. The entry holds the base URL (`EPIC_FHIR_BASE_URL`) and the names of the secrets (`EPIC_TOKEN_URL`, `EPIC_CLIENT_ID`, `EPIC_PRIVATE_KEY`, `EPIC_KID`) that the OAuth2 auth provider reads.

### `pyproject.toml` — dependency management

The FHIR integration relies on two dependencies, declared in the root `pyproject.toml` and in `packages/connectors/fhir_epic/pyproject.toml`:

- **`PyJWT[crypto]`**: Required to generate the RS384-signed JWTs for the SMART Backend Services authentication flow.
- **`httpx[http2]`**: Used as the primary asynchronous HTTP client for all FHIR API interactions, chosen for its performance and native `asyncio` support.

---

## Implementation details

### Authentication flow

The connector follows the **SMART Backend Services** specification for Epic:

1. **JWT creation**: The runtime's OAuth2 auth provider signs a JWT using `RS384` with the `EPIC_PRIVATE_KEY` secret. The JWT assertion lives for 5 minutes by default.
2. **Token exchange**: The provider posts the JWT to the `EPIC_TOKEN_URL` secret to obtain an `access_token` and caches it for the `expires_in` lifetime the server returns.
3. **Request execution**: The connector attaches the token as a `Bearer` header to subsequent FHIR API calls.

### Resilience and runtime

Each action execution is wrapped by the platform's **Base Runtime**:

- **`BaseConnector`**: Handles trace ID generation, logging, and performance metrics.
- **`resilience.py`**: Automatically applies **Exponential Backoff Retries** (Tenacity) and **Circuit Breaking** (PyBreaker) per action. If Epic returns a 503 or transient error, the system retries automatically before failing.

### Standardised payload output — `ConnectorResponse`

The AOT platform solves the "error chaos" problem by intercepting hundreds of unique API exceptions and mapping them into a unified, predictable taxonomy. Every action returns a consistent JSON payload, defined in `src/node_wire_runtime/models.py`:

```python
class ConnectorResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error_code: Optional[str] = None
    error_category: Optional[ErrorCategory] = None
    message: Optional[str] = None
    trace_id: str
    details: Optional[Any] = None
```

### Unified error taxonomy

By using this standardized model, client consumers can handle errors predictably. Whether the error stems from an HTTP timeout, a 401 Unauthorized from Epic, or a Pydantic validation failure, the response structure remains identical:

| Category | Description |
|---|---|
| `RETRYABLE` | Transient failures — safe to retry automatically |
| `BUSINESS` | Validation or business-rule violations |
| `AUTH` | Authentication or authorisation failures |
| `FATAL` | Unrecoverable errors requiring manual intervention |

---

## Manual verification

Exercise the connector with the REST `curl` examples in [`docs/connectors.md`](../../docs/connectors.md) / the Swagger UI at `http://localhost:8000/docs`, or the runnable scripts under `tests/playground/epic_fhir/`.

**Recommended test flow:**

1. **Read Patient** — Confirm basic connectivity.
2. **Search Encounter** — Required to find valid encounter IDs for clinical notes.
3. **Search DocumentReference** — Useful for discovering valid LOINC types supported by the specific Epic sandbox.
4. **Create DocumentReference** — Final end-to-end verification.

---

## Directory structure

The connector is organized as follows:

| File / Path | Purpose |
|---|---|
| `src/node_wire_fhir_epic/logic.py` | Core logic and action dispatch |
| `src/node_wire_fhir_epic/schema.py` | Pydantic input/output models |
| `src/bindings/factory.py` | Connector instantiation logic |
| `src/bindings/rest_api/app.py` | REST API routing |
| `src/node_wire_fhir_epic/normalizers.py` | MCP argument normalizers and search guards |
| `tests/test_fhir_epic.py` | Comprehensive test suite |
