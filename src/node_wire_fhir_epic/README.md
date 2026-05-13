<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# FHIR Epic Connector — Technical Documentation

> **Platform:** Node Wire
> **Standard:** FHIR R4
> **Auth Method:** SMART Backend Services — RS384 JWT / OAuth2
> **Actions:** `read_patient` · `search_encounter` · `create_document_reference` · `search_document_reference`
> **Source:** `src/node_wire_fhir_epic/`
> **Test Collection:** `postman_fhir_epic_collection.json`

---

## 1. Architecture Overview

The FHIR Epic connector is designed to interface with Epic EHR systems using the FHIR R4 standard. Unlike simpler connectors, this connector is **multi-action**, meaning a single configuration entry in `connectors.yaml` exposes multiple distinct operations (actions).

### Logic Consolidation

Initially, each action (e.g., `read_patient`, `search_encounter`) was implemented in its own class. This led to code duplication and a cluttered workspace. We refactored this into a single **"Fat Connector"** architecture:

- **`FhirEpicConnector`**: A single class that encapsulates all shared logic, authentication flows (JWT/OAuth2), and the specific implementation methods for each action.
- **`_FhirAction`**: A lightweight internal wrapper that inherits from `BaseConnector`. This allows the connector to remain compatible with the platform's standard routing and manifest generation while centralizing the actual execution logic.

---

## 2. Framework Integration & Decisions

To support this consolidated architecture while maintaining a clean codebase, several key changes were made to the platform's core bindings.

### `factory.py` — Multi-Action Support

The `ConnectorFactory` was updated to support connectors that manage many actions internally.

- **Design Decision**: Instead of the factory returning a list of 4 different connector instances for `fhir_epic`, it now returns **one instance** of the `FhirEpicConnector` class.
- **Action Discovery**: The factory uses `list_actions()` and `get_action(name)` helpers on the connector instance to discover and dispatch specific operations. This keeps the `_connectors` dictionary clean (one entry per `connector_id`).

### `app.py` — 422 Unprocessable Entity Fix

When generating dynamic REST routes, we encountered a critical `422` error where FastAPI would fail to validate the request body.

- **The Problem**: In dynamic route generation loops, FastAPI's automatic type introspection fails to correctly resolve Pydantic models when they are part of a Python closure. It would incorrectly fall back to treating the payload as a query parameter.
- **The Solution**: We updated the dynamic endpoint to accept `fastapi.Request` directly and parse the JSON body manually (`await request.json()`). This bypasses the faulty introspection layer while still using the Pydantic models for validation later in the `connector.run()` flow.

### Runtime Logging — Avoiding Collisions

We synchronized a change across `base.py` and `resilience.py` to rename the log attribute `message` to `error_message`.

- **The Problem**: The Python `logging` module uses `message` as a reserved internal attribute on `LogRecord` objects. Overwriting it in the `extra` dictionary can lead to lost data or crashes in some logging formatters.
- **The Solution**: By using `error_message`, we ensure that the technical details of the exception are preserved and distinctly visible in structured logging backends (like OpenTelemetry) without conflicting with the standard log event description.

### `config/connectors.yaml` — Connector Configuration

The central configuration was updated to include the `fhir_epic` entry.

- **Why**: This allows the `ConnectorFactory` to recognize and instantiate the FHIR connector. It contains the environment-specific URLs and credentials (like the JWT Key ID) needed for the Epic Sandbox.

### `pyproject.toml` — Dependency Management

Two new dependencies were added to support the FHIR integration:

- **`pyjwt[crypto]`**: Required to generate the RS384-signed JWTs for the SMART Backend Services authentication flow.
- **`httpx`**: Used as the primary asynchronous HTTP client for all FHIR API interactions, chosen for its performance and native `asyncio` support.

---

## 3. Implementation Details

### Authentication Flow

The connector follows the **SMART Backend Services** specification for Epic:

1. **JWT Creation**: Generates a signed JWT using `RS384` with the provided `epic_private_key`.
2. **Token Exchange**: Posts the JWT to the `epic_token_url` to obtain a short-lived (5 min) `access_token`.
3. **Request Execution**: Attaches the token as a `Bearer` header to subsequent FHIR API calls.

### Resilience and Runtime

Each action execution is wrapped by the platform's **Base Runtime**:

- **`BaseConnector`**: Handles trace ID generation, logging, and performance metrics.
- **`resilience.py`**: Automatically applies **Exponential Backoff Retries** (Tenacity) and **Circuit Breaking** (PyBreaker) per action. If Epic returns a 503 or transient error, the system will automatically retry before failing.

### Standardised Payload Output — `ConnectorResponse`

The AOT platform solves the "error chaos" problem by intercepting hundreds of unique API exceptions and mapping them into a unified, predictable taxonomy. Every action returns a consistent JSON payload:

```python
class ConnectorResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error_code: Optional[str] = None
    error_category: Optional[ErrorCategory] = None
    message: Optional[str] = None
    trace_id: str
```

### Unified Error Taxonomy

By using this standardized model, client consumers can handle errors predictably. Whether the error stems from an Axios timeout, a 401 Unauthorized from Epic, or a Pydantic validation failure, the response structure remains identical:

| Category | Description |
|---|---|
| `RETRYABLE` | Transient failures — safe to retry automatically |
| `BUSINESS` | Validation or business-rule violations |
| `AUTH` | Authentication or authorisation failures |
| `FATAL` | Unrecoverable errors requiring manual intervention |

---

## 4. Manual Verification

A Postman collection is provided at the root: `postman_fhir_epic_collection.json`.

**Recommended Test Flow:**

1. **Read Patient** — Confirm basic connectivity.
2. **Search Encounter** — Required to find valid encounter IDs for clinical notes.
3. **Search DocumentReference** — Useful for discovering valid LOINC types supported by the specific Epic sandbox.
4. **Create DocumentReference** — Final end-to-end verification.

---

## 5. Directory Structure

| File / Path | Purpose |
|---|---|
| `src/node_wire_fhir_epic/logic.py` | Core logic and action dispatch |
| `src/node_wire_fhir_epic/schema.py` | Pydantic input/output models |
| `src/bindings/factory.py` | Connector instantiation logic |
| `src/bindings/rest_api/app.py` | REST API routing |
| `tests/test_fhir_epic.py` | Comprehensive test suite |
