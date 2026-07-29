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
- **Layer C – Bindings** (`src/bindings/`): How the platform is exposed to the outside world—REST API, gRPC server, MCP server—and how connectors are loaded from configuration (ConnectorFactory + `config/connectors.yaml`).

<div class="nw-diagram-stack" markdown="1">

<div class="nw-diagram nw-diagram--row" markdown="1">

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "16px", "fontFamily": "Inter, system-ui, sans-serif", "primaryTextColor": "#E8EDF5", "lineColor": "#62d2f5"}, "flowchart": {"nodeSpacing": 36, "rankSpacing": 40, "padding": 16}}}%%
flowchart LR
    subgraph clients ["External Clients"]
        direction LR
        REST["REST clients"]
        GRPC["gRPC clients"]
        MCP["MCP / AI agents"]
    end

    classDef client fill:#1a3a4a,stroke:#37c4f0,stroke-width:2px,color:#E8EDF5
    class REST,GRPC,MCP client
    style clients fill:#151920,stroke:#37c4f0,stroke-width:2px,color:#37c4f0
```

</div>

<div class="nw-flow-connector nw-flow-down"><span>requests ↓</span></div>

<div class="nw-diagram nw-diagram--row" markdown="1">

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "16px", "fontFamily": "Inter, system-ui, sans-serif", "primaryTextColor": "#E8EDF5", "lineColor": "#62d2f5"}, "flowchart": {"nodeSpacing": 36, "rankSpacing": 40, "padding": 16}}}%%
flowchart LR
    subgraph layerC ["Layer C · Bindings · src/bindings/"]
        direction LR
        RestAPI["REST API<br/>FastAPI :8000"]
        GrpcSrv["gRPC Server<br/>:50051"]
        McpSrv["MCP Server"]
        Factory["ConnectorFactory"]
        Config["connectors.yaml"]
    end

    RestAPI --> Factory
    GrpcSrv --> Factory
    McpSrv --> Factory
    Config -. "loads" .-> Factory

    classDef bindings fill:#243044,stroke:#37c4f0,stroke-width:2px,color:#E8EDF5
    classDef config fill:#242930,stroke:#8A9BAC,stroke-width:2px,color:#E8EDF5
    class RestAPI,GrpcSrv,McpSrv,Factory bindings
    class Config config
    style layerC fill:#151920,stroke:#37c4f0,stroke-width:2px,color:#37c4f0
```

</div>

<div class="nw-flow-connector nw-flow-down"><span>ConnectorFactory ↓</span></div>

<div class="nw-diagram nw-diagram--row nw-diagram--runtime" markdown="1">

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "40px", "fontFamily": "Inter, system-ui, sans-serif", "primaryTextColor": "#E8EDF5", "lineColor": "#62d2f5"}, "flowchart": {"nodeSpacing": 156, "rankSpacing": 40, "padding": 16}}}%%
flowchart LR
    subgraph layerA ["Layer A · Runtime · src/node_wire_runtime/"]
        direction LR
        Validate["Pydantic validation"]
        Policy["PolicyHook"]
        Resilience["Retries & circuit breaker"]
        Errors["ErrorMapper"]
        Otel["OpenTelemetry"]
    end

    Validate --> Policy
    Policy --> Resilience
    Resilience --> Errors
    Otel -. "traces" .-> Resilience

    classDef runtime fill:#3a3420,stroke:#ecb32e,stroke-width:2px,color:#E8EDF5
    classDef telemetry fill:#242930,stroke:#8A9BAC,stroke-width:2px,color:#E8EDF5
    class Validate,Policy,Resilience,Errors runtime
    class Otel telemetry
    style layerA fill:#151920,stroke:#ecb32e,stroke-width:2px,color:#ecb32e
```

</div>

<div class="nw-flow-connector nw-flow-down"><span>BaseConnector.run ↓</span></div>

<div class="nw-diagram nw-diagram--row" markdown="1">

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "16px", "fontFamily": "Inter, system-ui, sans-serif", "primaryTextColor": "#E8EDF5", "lineColor": "#62d2f5"}, "flowchart": {"nodeSpacing": 32, "rankSpacing": 36, "padding": 16}}}%%
flowchart TB
    subgraph layerB ["Layer B · Connectors · src/node_wire_*/"]
        direction TB
        subgraph bRow1 [" "]
            direction LR
            GDrive["Google Drive"]
            SMTP["SMTP"]
            Stripe["Stripe"]
            FHIR["FHIR Epic/Cerner"]
        end
        subgraph bRow2 [" "]
            direction LR
            SFDC["Salesforce"]
            Slack["Slack"]
            HTTP["HTTP Generic"]
        end
    end

    classDef connector fill:#3a2430,stroke:#e01d5a,stroke-width:2px,color:#E8EDF5
    class GDrive,SMTP,Stripe,FHIR,SFDC,Slack,HTTP connector
    style layerB fill:#151920,stroke:#e01d5a,stroke-width:2px,color:#e01d5a
    style bRow1 fill:transparent,stroke:transparent,color:transparent
    style bRow2 fill:transparent,stroke:transparent,color:transparent
```

</div>

<div class="nw-flow-connector nw-flow-down"><span>outbound calls ↓</span></div>

<div class="nw-diagram nw-diagram--row" markdown="1">

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "16px", "fontFamily": "Inter, system-ui, sans-serif", "primaryTextColor": "#E8EDF5", "lineColor": "#62d2f5"}, "flowchart": {"nodeSpacing": 36, "rankSpacing": 40, "padding": 16}}}%%
flowchart LR
    subgraph external ["External Systems"]
        ThirdParty["Third-party APIs & services"]
    end

    classDef ext fill:#1a3338,stroke:#62d2f5,stroke-width:2px,color:#E8EDF5
    class ThirdParty ext
    style external fill:#151920,stroke:#62d2f5,stroke-width:2px,color:#62d2f5
```

</div>

<div class="nw-flow-connector nw-flow-up"><span>↑ ConnectorResponse returns through Layer C to clients</span></div>

</div>

### Data Flow (Simplified)

1. A request arrives via REST, gRPC, or MCP.
2. The `ConnectorFactory` resolves the right connector.
3. The runtime runs the connector:
   - Validate input via Pydantic.
   - Optional policy check.
   - Retry/circuit-breaker wrapper (resilience).
   - Execute internal logic.
   - Map any exceptions to the standard error taxonomy.
4. The response is returned in a standard shape (`ConnectorResponse`).

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
- **PolicyHook**: Optional hook to allow or deny execution based on principal or tenant.
- **Telemetry**: OpenTelemetry integration for tracing.

---

## Layer B – `connectors`

**Purpose:** System adapters that talk to external services. Each connector defines input/output models and implements `internal_execute`.

**Location:** `src/node_wire_<name>/`

### Common Structure

- `schema.py`: Pydantic models for request and response.
- `logic.py`: Connector class and external service logic.
- `registration.py`: Registers connector-specific exceptions.

---

## Layer C – `bindings`

**Purpose:** Expose connectors over different protocols and load them from configuration.

**Location:** `src/bindings/`

### Bindings Offered

- **REST API (FastAPI)**: Dynamic routes at `POST /connectors/{connector_id}/{action}`.
- **gRPC Server**: Protocol buffers based interface on port 50051.
- **MCP Server**: Model Context Protocol implementation for AI agents.
