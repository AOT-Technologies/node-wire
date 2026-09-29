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

## Quick start

You need Python 3.13+ and [`uv`](https://docs.astral.sh/uv/). The [Installation guide](installation.md) covers the rest.

```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
uv sync --frozen --extra agents --dev
cp sample.env .env                  # set NW_ALLOWED_CONNECTORS, e.g. http_generic
export NW_REST_AUTH_DISABLED=true   # local dev only — otherwise /connectors/* and /ready return 503 until auth is configured
MODE=API uv run node-wire
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the Swagger UI, or the [playground](http://localhost:8000/playground/) for interactive connector demos.

## Where to start

<div class="grid cards" markdown>

-   **Run it**

    Install, configure the `NW_*` variables, and turn on multi-tenancy.

    [:octicons-arrow-right-24: Installation](installation.md) · [Configuration](configuration.md) · [Tenancy](architecture/tenancy.md)

-   **Call a connector**

    Use a shipped connector in-process or over REST, per tenant.

    [:octicons-arrow-right-24: Use a connector](connectors.md) · [Connector catalog](connector-reference.md#connector-catalog)

-   **Build or generate a connector**

    Write one by hand, or generate one from an OpenAPI spec with `nw`.

    [:octicons-arrow-right-24: Build a connector](connectors-build.md) · [CLI](cli/index.md)

-   **Deploy MCP**

    Expose connectors to AI agents: one process, per-connector images, or `nw gen-stacklok`.

    [:octicons-arrow-right-24: MCP overview](mcp.md#which-mcp-path)

</div>

New to the codebase? The [reading map](reading.md) gives the order: architecture, domain, seams, the `run()` pipeline, then topic branches. The reasons behind past decisions are in the [ADRs](adr/index.md).

Shipped connectors: Google Drive, Salesforce, Slack, SMTP, Stripe, FHIR Epic, FHIR Cerner and HTTP Generic. See the [catalog](connector-reference.md#connector-catalog).

## Contributing

Contributions are welcome. See the [Contributing guide](contributing.md) for development setup, quality checks, and DCO requirements, and [Changing the docs](reading.md#changing-the-docs) before you edit a page.
