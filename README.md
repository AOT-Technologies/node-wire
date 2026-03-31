# Node Wire

This repository implements **Node Wire**: a three-layer Python platform that runs connector adapters over REST, gRPC, or MCP. Each connector talks to an external system (e.g. Google Drive, SMTP, Stripe); the runtime provides a consistent execution contract, error handling, and resilience. This is a POC—intended to validate the architecture and be understandable for developers new to the codebase.

For dependency management use any tool that understands `pyproject.toml` (e.g. `uv`, `pip`, or `poetry`).

---

## Individual MCP servers

Each connector can run as its own independent MCP server (Docker image).

| Image                   | Tool exposed               | Docker image                     |
| ----------------------- | -------------------------- | -------------------------------- |
| `nw-google-drive`       | `google_drive_upload_file` | `docker/google-drive/Dockerfile` |
| `nw-smartonfhir-epic`   | `fhir_epic_read_patient`   | `docker/fhir-epic/Dockerfile`    |
| `nw-smartonfhir-cerner` | `fhir_cerner_read_patient` | `docker/fhir-cerner/Dockerfile`  |
| `nw-smtp`               | `smtp_send_email`          | `docker/smtp/Dockerfile`         |

See [docs/mcp-servers.md](docs/mcp-servers.md) for build, env config, docker-compose, and ToolHive registration.

---

## High-level architecture

The platform is split into three layers:

- **Layer A – Runtime** (`runtime`): The engine that every connector runs inside. It defines the execution contract, a standard error taxonomy, retries and circuit breaking, and telemetry.
- **Layer B – Connectors** (`connectors`): Adapters that implement that contract and call external systems (HTTP Generic, SMTP, Stripe, Google Drive, FHIR Epic, FHIR Cerner). Each connector has its own input/output schema and business logic.
- **Layer C – Bindings** (`bindings`): How the platform is exposed to the outside world—REST API, gRPC server, MCP server—and how connectors are loaded from configuration (ConnectorFactory + `config/connectors.yaml`).

**Data flow (simplified):** A request arrives via REST, gRPC, or MCP → the factory resolves the right connector → the runtime runs it (validate input → optional policy check → retry/circuit-breaker wrapper → execute) → the response is returned in a standard shape (`ConnectorResponse`).

---

## Layer A – `runtime`

**Purpose:** Provide shared execution and reliability so every connector behaves in a consistent way (validation, errors, retries, telemetry) without each connector reimplementing the same plumbing.

**Location:** `src/runtime/` (base.py, models.py, errors.py, resilience.py, secrets.py, policy.py).

### Main pieces

- **BaseConnector**  
  Abstract base class for all connectors. Subclasses implement `internal_execute(...)`. The runtime’s `run()` method:
  1. Generates a trace ID and starts an OpenTelemetry span.
  2. Validates the raw request body with Pydantic (using the connector’s input model).
  3. Calls the optional policy hook (if configured).
  4. Wraps execution with retries and a circuit breaker (resilience).
  5. Maps any exception to the standard error taxonomy.
  6. Returns a `ConnectorResponse` (success + data, or error_code + error_category + message).

- **ConnectorResponse / ErrorCategory**  
  Every connector returns the same response shape: `success`, `data`, `error_code`, `error_category`, `message`, `trace_id`. Categories are `RETRYABLE`, `BUSINESS`, `AUTH`, `FATAL`. Bindings (e.g. REST) map these to HTTP status codes (e.g. BUSINESS → 400, AUTH → 401, RETRYABLE → 503, FATAL → 500).

- **ErrorMapper**  
  A registry that maps exception types to a stable error code and category. Connectors register their own exception types in their Layer B `registration` module. Unmapped exceptions default to FATAL.

- **Resilience**  
  A decorator (e.g. Tenacity for retries, PyBreaker for circuit breaker) wraps the actual execution. Transient failures are retried; after too many failures the circuit opens to avoid overloading the external system.

- **SecretProvider**  
  Abstraction for fetching secrets (API keys, credentials). The POC uses environment variables via `EnvSecretProvider` in the factory. Connectors receive the provider and use it to resolve connector-specific keys (e.g. Google Drive’s service account JSON).

- **PolicyHook**  
  Optional hook to allow or deny execution (e.g. by principal or tenant). Not required for the POC; when present, the runtime calls it after validation and before execution.

- **Telemetry**  
  OpenTelemetry span around `connector.run` with attributes such as connector id, action, trace id, tenant, principal.

---

## Layer B – `connectors`

**Purpose:** System adapters that talk to external services. Each connector defines input/output models and implements `internal_execute` (and optionally registers its own exceptions with the ErrorMapper).

**Location:** `src/connectors/`. Each connector lives in its own subpackage (e.g. `google_drive/`, `smtp/`, `stripe/`, `http_generic/`).

### Common structure per connector

- **schema.py** – Pydantic models for request (input) and response (output). Some connectors use a single action (e.g. `execute`) with a discriminated union in the payload (e.g. Google Drive: `action: "files.list" | "files.get" | ...`).
- **logic.py** – Connector class (subclass of `BaseConnector`) and the actual calls to the external SDK or API inside `internal_execute`.
- **registration.py** – Registers connector-specific exception types with `ErrorMapper` (category and optional error code). Loaded at startup via `auto_register()`.
- **exceptions.py** (optional) – Connector-specific exception classes.

### Connectors included

| Connector       | Description                                      | REST action   | Exposed via (from config)   |
|----------------|--------------------------------------------------|---------------|-----------------------------|
| **http_generic** | Generic HTTP request (any URL, method, headers)   | `request`     | rest, grpc, mcp             |
| **smtp**        | Send email via SMTP                              | `send_email`  | rest, grpc, mcp             |
| **stripe**      | Stripe charge                                    | `charge`      | grpc, mcp (no rest in config)|
| **google_drive**| Google Drive (list, create, get, update, upload, delete, permissions) | `execute` (payload discriminator) | rest, grpc, mcp |
| **fhir_epic**   | FHIR R4 integration for Epic (multi-action)      | `read_patient`, `search_encounter`, `create_document_reference`, `search_document_reference` | rest, grpc, mcp |
| **fhir_cerner** | FHIR R4 integration for Cerner (multi-action)    | `read_patient`, `search_encounter`, `create_document_reference`, `search_document_reference` | rest, grpc, mcp |

### Connector-specific documentation

**Details for each connector**—operations, request/response bodies, examples, and error handling—**are documented in that connector’s folder.**

Examples: Google Drive has a full doc at `src/connectors/google_drive/README.md`; FHIR connectors are documented at `src/connectors/fhir_epic/README.md` and `src/connectors/fhir_cerner/README.md`. Other connectors may have a similar `.md` in their folder or document behavior in code and docstrings; always check the connector’s folder for up-to-date details.

---

## Layer C – `bindings`

**Purpose:** Expose connectors over different protocols and load them from configuration. No business logic lives here—only routing, config, and protocol translation.

**Location:** `src/bindings/` (factory.py, rest_api/app.py, grpc_server/, mcp_server/), and the entrypoint `bindings_entrypoint.py` at the package root.

### ConnectorFactory

- Reads `config/connectors.yaml` (list of connectors with `enabled` and `exposed_via` per protocol: rest, grpc, mcp).
- Instantiates each enabled connector (with a shared `EnvSecretProvider`).
- `list_for_protocol("rest" | "grpc" | "mcp")` returns only connectors that are exposed for that protocol. Used by the REST app and MCP server to build routes or tool lists.

### REST API (FastAPI)

- **GET /health** – Health check.
- **GET /docs** – Swagger UI.
- Routes are built dynamically: **POST /connectors/{connector_id}/{action}** (e.g. `POST /connectors/google_drive/execute`). The request body is JSON; the response is the standard `ConnectorResponse`. HTTP status is derived from `error_category` (e.g. BUSINESS → 400, AUTH → 401, RETRYABLE → 503, FATAL → 500).

### gRPC / MCP

- **gRPC:** Started when `MODE=GRPC`; server listens on port 50051.
- **MCP:** Started when `MODE=MCP`; server exposes tools for discovery and invocation.

### Entrypoint

- Run with `python -m bindings_entrypoint` (or the `node-wire` script after install). The **MODE** environment variable selects:
  - **API** (default) – REST API on port 8000.
  - **GRPC** – gRPC server on port 50051.
  - **MCP** – MCP server.

---

## Configuration

- **config/connectors.yaml**  
  Lists each connector with:
  - `enabled`: whether to load it.
  - `exposed_via`: list of protocols (`rest`, `grpc`, `mcp`). Only listed protocols expose that connector.

- **Secrets**  
  Supplied via environment variables. The factory uses `EnvSecretProvider`; keys are connector-specific (e.g. Google Drive expects a variable documented in `src/connectors/google_drive/README.md`).

### Google Drive service account setup (quick)

1. In Google Cloud Console, select your project and enable **Google Drive API** (`APIs & Services` -> `Library`).
2. Go to `APIs & Services` -> `Credentials` -> `Create Credentials` -> `Service Account`.
3. Create a JSON key for that service account (`Keys` -> `Add Key` -> `Create new key` -> `JSON`).
4. Save the key file securely (example name: `service_account.json`) and never commit it to Git.
5. Open the JSON and copy the `client_email` value.
6. In Google Drive, share the target folder with that service-account email (Editor permission).
7. Copy that folder ID and use it in demo requests / env as needed.

Set credential secret used by this platform (`GOOGLE_DRIVE_SA_JSON`):

- **Option A (recommended for local):** set it to the absolute path of the JSON file.
- **Option B:** set it to the full JSON content as a string.

PowerShell example (load JSON content into env var for current shell):

```powershell
$saPath = "C:\path\to\service_account.json"
$env:GOOGLE_DRIVE_SA_JSON = Get-Content -Path $saPath -Raw
```

---

## Running the platform

1. **Install** from the repo root:
   ```bash
   pip install .
   ```

2. **Start the REST API** (default):
   - **Windows (cmd):** `set MODE=API && python -m bindings_entrypoint`  
     (Or omit `MODE`; API is the default.)
   - **Windows (PowerShell):** `$env:MODE="API"; python -m bindings_entrypoint`
   - **Linux/macOS:** `MODE=API python -m bindings_entrypoint`

   Then open:
   - **Health:** http://localhost:8000/health  
   - **Swagger:** http://localhost:8000/docs  

3. **Start gRPC or MCP**  
   Set `MODE=GRPC` or `MODE=MCP` using your shell’s syntax (same as above for Windows).

---

## Dependencies

All dependencies are declared in `pyproject.toml` (Python >=3.11). They include: pydantic, FastAPI, uvicorn, tenacity, pybreaker, OpenTelemetry, grpcio, and connector-specific libraries (httpx, aiosmtplib, stripe, google-auth, google-api-python-client, etc.). See `pyproject.toml` for the full list and versions.
