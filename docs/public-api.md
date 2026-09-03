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
- `BaseConnector`, `RestConnector` — base classes for connectors (`RestConnector` adds the shared HTTP-request scaffolding used by OpenAPI-generated connectors).
- `RestResponseOutput` — shared output model for `RestConnector`-based connectors.
- `get_connector_registry()` — returns a copy of the connector-id → class registry.
- `nw_action`, `sdk_action` — action decorators.
- `SdkActionSpec`, `default_build_kwargs`, `default_resolve_method`, `default_invoke`, `execute_spec_in_thread`, `execute_spec_async`.
- `navigate_resource` — deprecated; unused by the execute path, kept for backward compatibility.
- `NestedConnectorActionError`.

### Responses & errors
- `ConnectorResponse`
- `ErrorCategory`
- `ErrorMapper`

### Authentication
- `AuthProvider` (base), `NoAuthProvider`, `StaticTokenAuthProvider`,
  `ApiKeyQueryAuthProvider`, `OAuth2AuthProvider`, `ServiceAccountAuthProvider`.
- `CallerIdentity`, `build_caller_identity`.

### Policy
- `PolicyHook`, `PolicyDenied`.

### Tenancy (host apps)
- `resolve_tenant_id`, `tenant_from_headers`, `MissingTenantError`
- `TenantMismatchError` — factory instance tenant disagrees with `run(tenant_id=...)`
- `TenantIdentityMismatchError` — a caller-supplied tenant (header/session) disagrees with the authenticated JWT's tenant claim
- `DEFAULT_TENANT` (`__default__`)
- `effective_run_tenant_id`, `normalize_tenant_id`, `tenants_equivalent` — helpers for the pin contract

### Runtime config store
- `ConnectorConfigStore`, `ConfigRecord` — the per-(tenant, connector, config_name) runtime config store backing multi-tenant named configs.
- `ConfigNotFoundError`, `ConfigNameConflictError`, `DefaultDeletionError` — config-store error types.

### Secrets
- `SecretProvider` (base), `EnvSecretProvider`, `SecretNotFoundError`, `SecretProviderError`.
- `TenantSecretProvider`, `TenantSecretNotFoundError` — tenant-scoped secret resolution.

### Streaming
- `StreamSignal`, `stream_completion_log`, `resolve_stream_buffer_ms`, `BufferedStreamIterator`.

### Version
- `__version__`

## Connector contract (extensibility API)

Connector authors depend on these stable modules:

- `node_wire_runtime.base_connector` — `BaseConnector`, action decorators.
- `node_wire_runtime.auth.base` — `AuthProvider` interface.
- `node_wire_runtime.secrets.base` — `SecretProvider` interface.

`node_wire_runtime.mcp_contract` was removed (was never actually generic: it held
exactly one Google Drive-specific legacy-alias flag, mis-listed here as a stable
extensibility point). Its contents moved to `node_wire_google_drive.normalizers` —
connector-specific logic stays in the connector. Connector-specific argument
normalizers were never part of the stable surface for any other connector either;
this corrects the one place that had accidentally been documented as if it were.

Connectors register via the `node_wire.connectors` entry-point group.

**Bootstrap (not in `__all__`):** `node_wire_runtime.connector_registry.auto_register()` loads entry points at process startup (requires `NW_ALLOWED_CONNECTORS`). In-process usage typically goes through `bindings.factory.ConnectorFactory` after `auto_register()`, not direct registry access.

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
