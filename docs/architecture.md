<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Node Wire Architecture

The Node Wire platform is designed as a three-layer Python platform that runs connector adapters over REST, gRPC, or MCP. Each connector talks to an external system (e.g., Google Drive, SMTP, Stripe); the runtime provides a consistent execution contract, error handling, and resilience.

## High-Level Architecture

The platform is split into three layers:

- **Layer A – Runtime** (`src/node_wire_runtime/`): The engine that every connector runs inside. It defines the execution contract, a standard error taxonomy, retries and circuit breaking, and telemetry.
- **Layer B – Connectors** (`src/node_wire_<connector>/`): Adapters that implement that contract and call external systems (HTTP Generic, SMTP, Stripe, Google Drive, FHIR Epic, FHIR Cerner, Salesforce, Slack). Each connector has its own input/output schema and business logic.
- **Layer C – Bindings** (`src/bindings/`): How the platform is exposed to the outside world—REST API, gRPC server, MCP server—and `ConnectorFactory`, which builds tenant-pinned connector instances. The factory reads `config/connectors.yaml` at startup (an external input, not part of the layer) and bootstraps it into the runtime `ConnectorConfigStore` (Layer A).

External callers reach the platform over REST, gRPC, or MCP. Each binding adapter resolves
transport-specific tenant and caller identity, then hands off to the shared invoke seam.

```mermaid
flowchart TB
    subgraph input["Deployment input · outside the layers"]
        Config[/"config/connectors.yaml<br/>or NW_CONFIG_PATH"/]
    end

    subgraph layerC["Layer C · Bindings · src/bindings/"]
        RestAPI["REST API<br/>FastAPI :8000"]
        GrpcSrv["gRPC server<br/>:50051"]
        McpSrv["MCP server<br/>stdio · HTTP"]
        Invoke["invoke.py · shared seam<br/>exposure → factory.get → normalize"]
        Factory["ConnectorFactory"]
    end

    subgraph layerA["Layer A · Runtime · src/node_wire_runtime/"]
        Store[("ConnectorConfigStore<br/>per-tenant configs")]
        Run["BaseConnector.run<br/>validate → policy → resilience → error mapping"]
    end

    subgraph layerB["Layer B · Connectors · src/node_wire_*/"]
        Pkg["Package per connector<br/>schema.py · discriminated union<br/>logic.py · class + error_map<br/>normalizers.py · optional"]
        Actions["Connector class · BaseConnector or RestConnector<br/>@sdk_action / @nw_action / action_specs"]
        Shipped["Shipped connectors<br/>google_drive · smtp · stripe · http_generic<br/>salesforce · slack · fhir_epic · fhir_cerner"]
    end

    Ext[["External systems · third-party APIs"]]

    RestAPI -- "tenant: header / JWT" --> Invoke
    GrpcSrv -- "tenant: metadata" --> Invoke
    McpSrv -- "tenant: session / pin" --> Invoke
    Invoke -- "1 · get(tenant, config)" --> Factory
    Invoke -- "2 · run() → ConnectorResponse" --> Run
    Config -. "read by load()" .-> Factory
    Factory -- "load(): bootstrap __default__<br/>get(): resolve config" --> Store
    Factory -. "builds tenant-pinned instance<br/>injects SecretProvider · AuthProvider · PolicyHook" .-> Run
    Run -- "internal_execute" --> Actions
    Actions --> Ext
    Run ~~~ Pkg
    Run ~~~ Shipped

    classDef binding fill:#ddf1fb,stroke:#1a88b0,stroke-width:1px,color:#0d2f3d
    classDef store fill:#f2f4f7,stroke:#8a9bac,stroke-width:1px,color:#1c2733
    classDef runtime fill:#fdf2d6,stroke:#b8860b,stroke-width:1px,color:#3a2c05
    classDef connector fill:#fde2ea,stroke:#b81548,stroke-width:1px,color:#3d0a1d
    classDef ext fill:#ddf5f0,stroke:#12867a,stroke-width:1px,color:#0b2f2a
    class RestAPI,GrpcSrv,McpSrv,Invoke,Factory binding
    class Config store
    class Store,Run runtime
    class Actions,Pkg,Shipped connector
    class Ext ext
    style input fill:#f7f8fa,stroke:#8a9bac,stroke-width:1px,stroke-dasharray:4 3,color:#1c2733
    style layerC fill:#f4fbfe,stroke:#1a88b0,stroke-width:1px,color:#0d2f3d
    style layerA fill:#fffaf0,stroke:#b8860b,stroke-width:1px,color:#3a2c05
    style layerB fill:#fff5f8,stroke:#b81548,stroke-width:1px,color:#3d0a1d
```

### Request lifecycle

```mermaid
%%{init: {"sequence": {"actorMargin": 28, "width": 110, "boxMargin": 6, "noteMargin": 6, "messageMargin": 28}}}%%
sequenceDiagram
    autonumber
    participant C as Client
    participant B as Binding
    participant I as invoke.py
    participant R as run()
    participant X as Connector

    C->>B: request
    Note over B: resolve tenant<br/>+ config
    B->>I: invoke(id, action, payload)
    Note over I: exposure check<br/>factory.get<br/>normalize args
    I->>R: run(payload, scopes)
    Note over R: validate → policy<br/>→ resilience
    R->>X: internal_execute
    X->>X: outbound call
    X-->>R: result / exception
    Note over R: ErrorMapper
    R-->>I: ConnectorResponse
    I-->>B: ConnectorResponse
    B-->>C: encoded response
```

A failure anywhere in `run` is mapped by `ErrorMapper` into the same `ConnectorResponse`
envelope with an `ErrorCategory` — bindings translate that to an HTTP status, a gRPC code,
or an MCP tool error, but the connector contract is identical on all three.

---

## Layer A – `runtime`

**Purpose:** Provide shared execution and reliability so every connector behaves in a consistent way (validation, errors, retries, telemetry) without each connector reimplementing the same plumbing.

**Location:** `src/node_wire_runtime/`

### Main Components

- **BaseConnector**: Abstract base class for all connectors. It handles the `run()` method pipeline.
- **ConnectorResponse / ErrorCategory**: Unified response shape and error categorization (`RETRYABLE`, `BUSINESS`, `AUTH`, `FATAL`).
- **ErrorMapper**: Maps exception types to stable error codes and categories.
- **Resilience**: Decorators for retries (Tenacity) and circuit breaking (PyBreaker).
- **SecretProvider**: Abstraction for fetching secrets (API keys, credentials).
- **PolicyHook**: Allow/deny check before execution, based on principal, scopes, or tenant. The hook type is pluggable. `BaseConnector` accepts `policy_hook=None`, but `ConnectorFactory` always attaches one: `ScopePolicyHook` when an MCP scope map or `NW_MCP_SCOPE_POLICY_DEFAULT=deny` is configured, otherwise `TenantConfigHook` (a tenant must have a config for the connector).
- **Tenant pinning**: Factory-built instances carry `_tenant_id`; `run()` uses that pin when `tenant_id` is omitted and rejects mismatched caller ids with `TENANT_MISMATCH`.
- **Telemetry**: OpenTelemetry integration for tracing.

### The `run()` pipeline

Every action goes through the same sequence. The tenant pin is checked before the
trace span opens; everything after it runs inside the span.

```mermaid
flowchart TB
    In(["run(payload)"]) --> Pin["tenant pin check"]
    Pin --> Val
    Pin -. "TENANT_MISMATCH" .-> Err

    Val["Pydantic validation<br/>discriminated union on action"]
    Pol["PolicyHook · allow / deny<br/>always set on factory instances"]
    Res["Retries + circuit breaker<br/>Tenacity · PyBreaker"]
    Exec["internal_execute<br/>dispatch to Layer B"]
    Val --> Pol --> Res --> Exec
    Otel(["OpenTelemetry span"]) -. "wraps validate → error mapping" .-> Val

    Err["ErrorMapper<br/>stable code + ErrorCategory"]
    Pol -. "PolicyDenied" .-> Err
    Res -. "exception" .-> Err
    Exec --> Out(["ConnectorResponse"])
    Err --> Out

    classDef step fill:#fdf2d6,stroke:#b8860b,stroke-width:1px,color:#3a2c05
    classDef term fill:#eceff3,stroke:#5b7387,stroke-width:1px,color:#1c2733
    classDef err fill:#fde2ea,stroke:#b81548,stroke-width:1px,color:#3d0a1d
    class Pin,Val,Pol,Res,Exec step
    class In,Out term
    class Err err
    class Otel term
```

---

## Layer B – `connectors`

**Purpose:** System adapters that talk to external services. Each connector defines input/output models and declares its actions with `@sdk_action` (or its alias `@nw_action`) or `action_specs`. Connectors do not implement `internal_execute` — `BaseConnector.internal_execute` dispatches to the declared action. Hand-written connectors subclass `BaseConnector`; OpenAPI-generated ones subclass `RestConnector`. `ConnectorFactory` injects the tenant-scoped `SecretProvider`, `AuthProvider`(s), and `PolicyHook` when it builds an instance.

**Location:** `src/node_wire_<name>/`

### Common Structure

- `schema.py`: Pydantic models for request and response.
- `logic.py`: Connector class and external service logic, including an optional `error_map` class attribute that registers the connector's exception mappings — scoped to that connector's own `connector_id` so they can never be resolved for another connector's errors.
- `normalizers.py` (optional): MCP argument normalizers that map LLM aliases to canonical fields before `run()` (wired via `mcp_normalize` on the action).

---

## Layer C – `bindings`

**Purpose:** Expose connectors over different protocols and build tenant-pinned connector instances from configuration (read from `config/connectors.yaml`, held in the runtime config store).

**Location:** `src/bindings/`

### Binding invoke

REST, MCP, and gRPC share one invoke seam in `src/bindings/invoke.py`. Each binding adapter resolves transport-specific tenant and caller identity, then delegates exposure, factory resolution, ingress normalization, and `run()` to that module. Transport-only concerns (HTTP status mapping, MCP pagination guardrails, protobuf encoding) stay in the respective binding.

### Bindings Offered

- **REST API (FastAPI)**: Dynamic routes at `POST /connectors/{connector_id}/{action}`.
- **gRPC Server**: Protocol buffers based interface on port 50051.
- **MCP Server**: Model Context Protocol implementation for AI agents.
