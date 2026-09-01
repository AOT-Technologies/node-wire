# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`nw-cli`**: a new unified CLI (`nw`) for the OpenAPI → connector → wheel →
  MCP → Docker pipeline, with `gen-all` (one-shot), and standalone `gen-whl`,
  `gen-mcp`, and `docker-build` stages, prerequisite checks, and a Rich
  progress UI (`nw-cli/`, `docs/nw-cli.md`).
- **`nw-connector-builder`**: a new package that derives a connector
  (auth, naming, operations, normalizers) from an OpenAPI/Swagger spec and
  generates its codegen output, with a promotion gate for reviewing generated
  connectors before they're wired in (`nw-connector-builder/`,
  `docs/nw-connector-builder.md`, `docs/nw-connector-builder-scope.md`).
- **`nw-mcp-builder`**: a new package that generates a standalone MCP server
  project from an existing connector (`nw-mcp-builder/`), including
  mcp-builder YAML configs for the Slack and Salesforce connectors.
- Multi-tenancy support across all bindings (REST, gRPC, MCP) and the
  playground: per-tenant configuration and secrets, tenant selection, and
  per-tenant credential isolation for every connector (Google Drive, HTTP
  generic, SMTP, Stripe, Epic FHIR, Cerner FHIR, Salesforce, Slack).
- `node_wire_runtime.config_store` and `node_wire_runtime.tenant_persistence`
  for durable per-tenant configuration storage, an expanded `secrets`
  subsystem for per-tenant secret resolution, and `node_wire_runtime.identity`
  for tenant identity resolution shared across bindings.
- `TenantSessionOverlay` (`node_wire_runtime.tenant_session`), extracted from
  the MCP server's private state, to isolate per-request tenant/config context.
- OIDC support for the Google Drive MCP server.
- LLM provider/model switching in the playground (`agents/llm_factory.py`,
  new `agents/llm_base.py`), with new Anthropic and Gemini providers alongside
  updated OpenAI and Groq providers, and playground UI controls for selecting
  a model per session.
- Per-connector response normalizers (`normalizers.py`) for Google Drive,
  Salesforce, SMTP, Epic FHIR, and Cerner FHIR.
- Error taxonomy support in the connector codegen and mcp-builder pipelines.
- A new query-parameter API key auth mechanism (`node_wire_runtime.auth.apikey_query`).
- OpenTelemetry metrics and an audit trail; a test-coverage gate for PRs
  (raised to 85%, with an 80% floor enforced on changed files).
- A new documentation site (`mkdocs.yml`, `docs/index.md`, branded stylesheets
  and logo assets) and `docs/mcp-servers.md` documenting multi-tenant MCP
  server setup.
- `scripts/build-mcp-server.sh` and `scripts/mcp-servers.registry` for
  building per-connector MCP server images, and a `docs.yml` CI workflow to
  publish the documentation site.
- `create-tag.yml` CI workflow for release tagging.

### Changed

- Collapsed the REST/gRPC binding invoke path into a shared `bindings/invoke.py`
  helper, removing duplicated dispatch logic between transports.
- Strengthened auth/scope-policy enforcement and connector isolation as part
  of a connector framework optimization pass; removed the per-connector
  `registration.py` modules and the shared `mcp_contract.py` /
  `mcp_normalizers.py` in favor of the new normalizer-based registration path.
- Rebuilt the wheel-build script for roughly an 80% reduction in build time.
- Pinned the MCP SDK version until the codebase is compatible with the newer
  release.
- `nw-mcp-builder` generated Dockerfiles now use the same digest-pinned
  `python:3.12-slim` base and non-root `USER` as checked-in images, with a
  whitelist `.dockerignore`, no secrets/`COPY .env` in the image, a read-only
  application tree, and no default `NW_MCP_AUTH_DISABLED` in containers.
- Node Wire branding refresh (README badges/logos, docs site theme).
- Updated `docs/architecture.md`, `docs/configuration.md`, `docs/connectors.md`,
  `docs/mcp.md`, `docs/nw-connector-builder.md`, `docs/public-api.md`, and
  `docs/toolhive_agent_scenario.md` for multi-tenancy, the connector builder
  pipeline, and the connector framework changes.

### Fixed

- `ErrorMapper` now scopes error-code matching per connector ID with an
  MRO-specific match, fixing a cross-connector leak where one connector's
  error code could surface through another connector's error handling.
- Fixed a cross-tenant/cross-config race condition on the MCP HTTP transport
  where concurrent requests could read another tenant's session or
  configuration.
- Fixed health check reporting.

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

- Connector authentication misconfiguration now surfaces clear, actionable error
  messages instead of cryptic library exceptions:
  - **OAuth2 private_key_jwt** (`oauth2.py`): `jwt.InvalidKeyError` is caught and
    re-raised with the algorithm and a pointer to the `private_key_secret`
    configuration.
  - **OAuth2 token endpoint** (`oauth2.py`): Non-200 responses from the token URL
    now include the HTTP status, the token URL, and a preview of the server
    response rather than a bare `raise_for_status()` traceback.
  - **Google service account** (`service_account.py`, `google_drive/logic.py`):
    Invalid JSON reports the secret name; a missing key file reports the resolved
    path; malformed key structures surface the underlying Google library error
    with context.
  - **SMTP** (`smtp/logic.py`): `SMTPAuthenticationError` names
    `SMTP_USERNAME`/`SMTP_PASSWORD`; connect/disconnect errors name
    `SMTP_HOST`/`SMTP_PORT`/`SMTP_USE_TLS`; timeout errors mention `NW_TIMEOUT`.

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

[1.0.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v1.0.0
[0.1.0]: https://github.com/AOT-Technologies/node-wire/releases/tag/v0.1.0
