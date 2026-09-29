<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# <img src="images/nw-favicon-black.png" alt="" width="90" align="center"/>node wire

[![CI](https://github.com/AOT-Technologies/node-wire/actions/workflows/pytest.yml/badge.svg)](https://github.com/AOT-Technologies/node-wire/actions/workflows/pytest.yml)
[![CodeQL](https://github.com/AOT-Technologies/node-wire/actions/workflows/codeql.yml/badge.svg)](https://github.com/AOT-Technologies/node-wire/actions/workflows/codeql.yml)
[![PyPI runtime](https://img.shields.io/pypi/v/node-wire-runtime.svg?label=node-wire-runtime)](https://pypi.org/project/node-wire-runtime/)
[![GitHub Release](https://img.shields.io/github/v/release/AOT-Technologies/node-wire)](https://github.com/AOT-Technologies/node-wire/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE)

<p align="center">
  <img src="images/nw-primary-logo-tag.png" alt="Node Wire — Layered Connector Framework" />
</p>

Node Wire is a three-layer Python platform that runs connector adapters (Google Drive, SMTP, Stripe, FHIR, Salesforce, Slack, and more) and exposes them over REST, gRPC, or MCP. It provides a consistent execution contract with built-in validation, resilience, and telemetry.

## Prerequisites

Before getting started, see the [Installation guide](installation.md) for full setup. You will need Python 3.13+, `uv` (recommended) or `pip`, Git, and optionally Docker (MCP server images) and Node.js (MCP Inspector).

## Quick Start

```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
uv sync --frozen --extra agents --dev
cp sample.env .env
export NW_REST_AUTH_DISABLED=true   # local dev only — otherwise /connectors/* and /ready return 503 until auth is configured
MODE=API uv run node-wire
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the Swagger UI, or the [playground](http://localhost:8000/playground/) for interactive connector demos.

## Key Sections

<div class="grid cards" markdown>

-   **Getting Started**

    Set up your environment and configure connectors.

    [:octicons-arrow-right-24: Installation](installation.md)

-   **Architecture**

    Understand the three-layer design: Runtime, Connectors, and Bindings.

    [:octicons-arrow-right-24: Architecture](architecture.md)

-   **Connectors**

    Build or configure integrations with Google Drive, Salesforce, Slack, and more.

    [:octicons-arrow-right-24: Use a connector](connectors.md) · [Build one](connectors-build.md)

-   **OpenAPI Builder**

    Generate a REST connector (and optional MCP host) from a Swagger/OpenAPI spec.

    [:octicons-arrow-right-24: nw-connector-builder](cli/nw-connector-builder.md)

-   **nw CLI**

    One-shot OpenAPI → connector → wheels → MCP host pipeline, plus `nw docker-build` for the image.

    [:octicons-arrow-right-24: CLI](cli/index.md)

-   **MCP Integration**

    Deploy connectors as Model Context Protocol servers for AI agents.

    [:octicons-arrow-right-24: MCP Overview](mcp.md)

-   **Multi-tenancy**

    Isolate tenants by header/JWT, with per-tenant named configs and secrets.

    [:octicons-arrow-right-24: Multi-tenancy](configuration.md#multi-tenancy)

</div>

## Available Connectors

| Connector | Protocol | Doc |
|---|---|---|
| Google Drive | REST · service account | [Guide](google_drive_connector.md) |
| Salesforce | REST | [Guide](salesforce_connector.md) |
| Slack | Web API | [Guide](slack_connector.md) |
| SMTP | Email | [Connectors](connectors.md) |
| Stripe | REST | [Connectors](connectors.md) |
| FHIR Epic | SMART on FHIR | [Connectors](connectors.md) |
| FHIR Cerner | SMART on FHIR | [Connectors](connectors.md) |
| HTTP Generic | REST bridge | [Connectors](connectors.md) |

## Which tool do I use?

`nw` is the only CLI most people need. It drives `nw-connector-builder`, `nw-mcp-builder`, and the vendored stacklok builder for you — start at the [CLI module](cli/index.md).
Reach for a standalone builder only when you need a stage on its own.

| I want to… | Use | Page |
|---|---|---|
| Turn an OpenAPI/Swagger spec into a connector, wheels and MCP host | **`nw gen-all`** | [nw CLI](cli/nw-cli.md) |
| Build the Docker image for that MCP host | `nw docker-build` | [nw CLI](cli/nw-cli.md) |
| Build an MCP server whose tools are curated by AI scoping | **`nw gen-stacklok`** | [stacklok MCP servers](stacklok-mcp-servers.md) |
| Call an existing connector, per tenant | *no CLI* — `ConnectorFactory` or the REST API | [Use a connector](connectors.md) |
| Write a connector by hand (SDK-style or non-REST) | *no CLI* — author `schema.py` + `logic.py` | [Build a connector](connectors-build.md) |
| Regenerate just the MCP host for an existing connector | `nw gen-mcp` | [nw CLI](cli/nw-cli.md) |
| Build just the wheels | `nw gen-whl` | [Packaging](packaging.md) |
| Deploy an MCP server to ToolHive and drive it with an agent | *no CLI* — `thv` + the bundled agent | [ToolHive scenario](toolhive_agent_scenario.md) |

**Advanced / direct access.** `nw-connector-builder` and `nw-mcp-builder` remain supported
as standalone entry points and expose the full flag surface, but `nw` calls both for you.
Read [nw-connector-builder](cli/nw-connector-builder.md) or [nw-mcp-builder](cli/nw-mcp-builder.md)
when you need a flag `nw` does not pass through, or are debugging a single stage.

## Docs map

| Area | Pages |
|---|---|
| MCP | [Overview](mcp.md) · [stacklok MCP servers](stacklok-mcp-servers.md) · [stacklok builder decisions](stacklok-mcp-builder-requirements.md) · [Client OAuth](mcp-client-oauth.md) · [ToolHive scenario](toolhive_agent_scenario.md) |
| Packaging & release | [Packaging](packaging.md) · [Versioning](versioning.md) · [Release rollback](release-rollback.md) · [Local wheels → images](local-packages-to-images.md) |
| Development | [Contributing](contributing.md) · [Code quality](code-quality-compliance.md) · [Quality & security gates](quality-security-gates.md) · [Public API](public-api.md) · [Troubleshooting](troubleshooting.md) |
| Compliance | [Privacy](privacy.md) · [HIPAA considerations](compliance/hipaa-considerations.md) |
| Connectors | [Use](connectors.md) · [Build](connectors-build.md) · [Reference](connector-reference.md) · [REST/MCP/gRPC exposure](connector-bindings.md) |
| CLI | [Overview](cli/index.md) · [nw CLI](cli/nw-cli.md) · [nw-connector-builder](cli/nw-connector-builder.md) · [Scope](cli/nw-connector-builder-scope.md) · [Codegen behaviour](cli/nw-connector-builder-codegen.md) · [nw-mcp-builder](cli/nw-mcp-builder.md) |
| Project | [Code of Conduct](https://github.com/AOT-Technologies/node-wire/blob/main/CODE_OF_CONDUCT.md) · [Governance](https://github.com/AOT-Technologies/node-wire/blob/main/GOVERNANCE.md) · [Support](https://github.com/AOT-Technologies/node-wire/blob/main/SUPPORT.md) · [Security](https://github.com/AOT-Technologies/node-wire/blob/main/SECURITY.md) · [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md) |

## Contributing

Contributions are welcome. See the [Contributing guide](contributing.md) for development setup, quality checks, and DCO requirements.
