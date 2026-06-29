<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Public API Reference (1.x)

This page enumerates the **stable public API** covered by the
[versioning & stability policy](versioning.md). Anything not listed here — and
anything named with a leading underscore — is internal and may change without a
major version bump.

## `node_wire_runtime`

Stable top-level exports (`node_wire_runtime.__all__`):

### Connector authoring
- `BaseConnector` — base class for connectors.
- `get_connector_registry()` — returns a copy of the connector-id → class registry.
- `nw_action`, `sdk_action` — action decorators.
- `SdkActionSpec`, `default_build_kwargs`, `execute_spec_in_thread`, `navigate_resource`.
- `NestedConnectorActionError`.

### Responses & errors
- `ConnectorResponse`
- `ErrorCategory`
- `ErrorMapper`

### Authentication
- `AuthProvider` (base), `NoAuthProvider`, `StaticTokenAuthProvider`,
  `OAuth2AuthProvider`, `ServiceAccountAuthProvider`.
- `CallerIdentity`, `build_caller_identity`.

### Policy
- `PolicyHook`, `PolicyDenied`.

### Secrets
- `SecretProvider` (base), `EnvSecretProvider`, `SecretNotFoundError`, `SecretProviderError`.

### Streaming
- `StreamSignal`, `stream_completion_log`, `resolve_stream_buffer_ms`, `BufferedStreamIterator`.

### Version
- `__version__`

## Connector contract (extensibility API)

Connector authors depend on these stable modules:

- `node_wire_runtime.base_connector` — `BaseConnector`, action decorators.
- `node_wire_runtime.mcp_contract` — MCP tool contract flags.
- `node_wire_runtime.auth.base` — `AuthProvider` interface.
- `node_wire_runtime.secrets.base` — `SecretProvider` interface.

Connectors register via the `node_wire.connectors` entry-point group.

## Wire contracts

- **REST** — routes and request/response schemas served by the API binding
  (Swagger UI at `/docs`).
- **gRPC** — the `Connector` service defined by the committed protobuf contract.
- **MCP** — tool manifests exposed by the MCP servers (`nw-*`).

## Configuration

- `connectors.yaml` schema — see [configuration.md](configuration.md).
- `NW_*` environment variables — see [configuration.md](configuration.md).

## Console scripts

`node-wire`, `nw-google-drive`, `nw-smartonfhir-epic`, `nw-smartonfhir-cerner`,
`nw-smtp`, `nw-stripe`, `nw-salesforce`, `nw-slack`.
