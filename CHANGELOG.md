<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-10-22

### Added

- **`nw` CLI** (`nw-cli`): one command for the OpenAPI → connector → wheel → MCP server → Docker
  pipeline (`nw gen-all`), with standalone `gen-whl`, `gen-mcp` and `docker-build` stages. See
  `docs/nw-cli.md`.
- **`nw-connector-builder`**: generates a connector package from an OpenAPI 3.0–3.2 or Swagger 2.0
  spec, with flat tool arguments, the spec's descriptions, schema constraints enforced on input, and
  a promotion gate for review. Operations the builder can't handle are dropped and reported instead
  of failing the build. Field-by-field support is in `docs/cli/openapi-coverage.md`.
- **`nw-mcp-builder`**: generates a standalone MCP server project from a connector.
- **`nw gen-stacklok`**: builds a [stacklok mcp-builder](https://github.com/stacklok/mcp-builder)
  server from an `mcp-scope.yaml` on the node-wire runtime, with tenants routed by ToolHive
  `MCPRemoteProxy`. See `docs/stacklok-mcp-servers.md`.
- **Multi-tenancy** across REST, gRPC, MCP and the playground: per-tenant configuration and secrets,
  tenant selection, and credential isolation for every connector.
- **MCP tool-search mode** (`NW_MCP_TOOL_MODE=search`): replaces the full `tools/list` with
  `nw_search_tools` and `nw_call_tool` for large connectors.
- **`node-wire-runtime` 1.1.0**: `headers:` in `connectors.yaml` for fixed request headers, refresh
  token rotation for `oauth2`, `envelope_ok_field` for APIs that answer `200 OK` with
  `{"ok": false}`, `apikey_query` auth, OpenTelemetry metrics and audit events, and
  `host_logging` for MCP hosts.
- **`node-wire-connectors`** meta-package: installs the runtime and every public connector.
- Playground LLM provider switching (OpenAI, Groq, Anthropic, Gemini), a Grafana **Connector Calls &
  Logs** dashboard, a REST `/ready` endpoint, and a published documentation site.

### Changed

- **Every package is a pure-Python `py3-none-any` wheel** instead of Cython-compiled per-platform
  wheels, and Python 3.11–3.13 is supported across the repository
  ([ADR 0003](docs/adr/0003-python-3-11-to-3-13.md)).
- **One error taxonomy on every surface** (`docs/errors.md`). MCP failures are `isError: true` with
  a code, REST refusals return the error envelope, and a missing secret is `SECRET_NOT_FOUND` /
  `TENANT_SECRET_NOT_FOUND`. **Clients matching the old codes must update.**
- **REST wire encoding**: booleans are sent as `true` / `false`, form values as JSON, and text/XML
  bodies as text. Generated packages require `node-wire-runtime>=1.1.0`.
- **Circuit breaker** counts only upstream-health failures, once per invocation, and thresholds are
  configurable per connector (`NW_BREAKER_<CONNECTOR_ID>_FAIL_MAX` / `_RESET_TIMEOUT`).
- The generic HTTP connector publishes as `node-wire-http`.

### Security

- **The playground is opt-in.** **Upgrade note:** set `NW_REST_PLAYGROUND_ENABLED=true` (and
  `NW_REST_AUTH_DISABLED=true`) to use it locally.
- **The `/v1` config and secret admin API requires scopes** (`nw:config:read`, `nw:config:write`,
  `nw:secrets:write`, `nw:platform:admin`).
- **MCP streamable-HTTP is stateless** and resolves identity, tenant and upstream bearer per
  request; `GET /mcp` returns `405`.
- **Config documents can't send process secrets to a caller-chosen host.** **Upgrade note:** add
  overriding hosts to `NW_REST_ALLOWED_HOSTS`.
- **`NW_MCP_SCOPE_POLICY_DEFAULT` accepts only `allow` or `deny`**; any other value fails startup
  instead of silently becoming `allow`.
- Generated Dockerfiles use a digest-pinned base image, a non-root user and no secrets in the image.

### Fixed

- `ErrorMapper` no longer leaks one connector's error code into another connector's errors.
- A race on the MCP HTTP transport no longer lets concurrent requests read another tenant's session
  or configuration.
- `grpcio-tools` moved to the optional `grpc-codegen` extra, fixing Windows installs without a
  matching wheel.

## [1.0.0] - 2026-06-27

First stable release. The public API is now **frozen under Semantic Versioning** —
see [docs/versioning.md](docs/versioning.md) for the stability and deprecation
policy and [docs/public-api.md](docs/public-api.md) for the supported surface.

### Added

- Versioning, stability, and deprecation policy (`docs/versioning.md`).
- Public API reference enumerating the frozen surface (`docs/public-api.md`).
- `node_wire_runtime.__version__`.
- DCO sign-off enforcement, Dependabot, weekly secret scanning, and `SUPPORT` /
  `GOVERNANCE` docs.
- Automated GitHub Release workflow with version and changelog validation, SBOM
  generation, release manifest generation, and GitHub release artifact upload.
- Tag-based PyPI publish workflow with release prerequisite checks, wheel checksum
  artifacts, and Sigstore attestations.
- CI badges in `README.md` and cross-platform pytest coverage on Linux, macOS,
  and Windows for Python 3.11 and 3.12.
- Test-coverage gate (`fail_under`) and updated security-audit install guidance
  for monorepo connector packages.

### Changed

- Promoted from Beta to **Production/Stable**; all nine packages versioned `1.0.0`.
- Connectors now require `node-wire-runtime>=1.0.0`.
- REST API authentication is now scoped to `/connectors/*` only. The playground UI
  (`/playground/*`), scenario API (`/scenarios/*`), and OpenAPI docs (`/docs`,
  `/redoc`, `/openapi.json`) are publicly accessible without credentials, making
  demo and discovery workflows viable when auth is enabled.
- Release and packaging documentation now use the `1.0.0` release flow and
  versioned MCP image examples.

### Fixed

- Connector authentication misconfiguration (OAuth2, Google service account, SMTP) now reports
  clear, actionable errors naming the setting to fix instead of library exceptions.

## [0.1.0] - 2026-06-26

### Added

- Initial public release of the Node Wire platform: runtime, connectors, and bindings.
- Nine publishable Python packages: runtime plus eight connectors (HTTP generic, Google Drive, SMTP, Stripe, Epic FHIR, Cerner FHIR, Salesforce, Slack).
- REST, gRPC, and MCP entrypoints with authentication, scope policy, and observability hooks.
- Per-connector MCP Docker images and unified MCP server (`agents.mcp_entrypoint`).
- ToolHive agent scenario documentation and sample agent workflow.
- CI quality gates: Ruff, Mypy, pytest, Bandit, pip-audit, and REUSE compliance.
- Governance docs: contributing guide, security policy, code of conduct, privacy notes, and HIPAA considerations.

### Fixed

- gRPC protobuf stubs committed and importable for production startup.
- REST API no longer requires the optional `playground` package at import time.
- Dependency lockfile upgraded to resolve known CVEs in transitive packages.
- Packaging, publish workflow, and security scanning aligned on the nine-package surface.

[1.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.1.0
[1.0.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.0.0
[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0
