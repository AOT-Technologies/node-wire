<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# <img src="./docs/images/nw-favicon-black.png" alt="" width="90" align="center"/>node wire

[![CI](https://img.shields.io/github/actions/workflow/status/AOT-Technologies/node-wire/pytest.yml?branch=main&label=CI)](https://github.com/AOT-Technologies/node-wire/actions/workflows/pytest.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/AOT-Technologies/node-wire/codeql.yml?branch=main&label=CodeQL)](https://github.com/AOT-Technologies/node-wire/actions/workflows/codeql.yml)
[![PyPI runtime](https://img.shields.io/pypi/v/node-wire-runtime?label=node-wire-runtime)](https://pypi.org/project/node-wire-runtime/)
[![GitHub Release](https://img.shields.io/github/v/release/AOT-Technologies/node-wire)](https://github.com/AOT-Technologies/node-wire/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

<p align="center">
  <img src="./docs/images/nw-primary-logo-tag.png" alt="Node Wire — Layered Connector Framework" />
</p>

node wire is a three-layer Python platform that runs connector adapters (Google Drive, SMTP, Stripe, FHIR, Salesforce, Slack, and more) and exposes them over REST, gRPC, or MCP. It provides a consistent execution contract with built-in validation, resilience, and telemetry.

## Quick start

You need Python 3.13+ and [`uv`](https://docs.astral.sh/uv/). See [Installation](docs/installation.md) for everything else, including Windows commands. To start the REST API locally, run the following commands:

```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
uv sync --frozen --extra agents --dev
cp sample.env .env                  # set NW_ALLOWED_CONNECTORS, for example http_generic
export NW_REST_AUTH_DISABLED=true   # local dev only — otherwise /connectors/* and /ready return 503 until auth is configured
MODE=API uv run node-wire
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the Swagger UI, or the [playground](http://localhost:8000/playground/) for interactive connector demos.

To install the packages instead of a source checkout, `pip install node-wire-connectors` installs the runtime and every public connector. See [Client consumption model](docs/packaging.md#client-consumption-model) for installing a subset.

## Documentation

The full documentation is published at **[aot-technologies.github.io/node-wire](https://aot-technologies.github.io/node-wire/)** and lives in [`docs/`](docs/). Start with the following pages:

| I want to… | Read |
|---|---|
| Install, configure, run multi-tenant | [Installation](docs/installation.md) · [Configuration](docs/configuration.md) · [Tenancy](docs/architecture/tenancy.md) |
| Call a shipped connector | [Use a connector](docs/connectors.md) · [Connector catalog](docs/connector-reference.md#connector-catalog) |
| Build or generate a connector | [Build a connector](docs/connectors-build.md) · [CLI](docs/cli/index.md) |
| Deploy connectors as MCP servers | [MCP overview](docs/mcp.md) |
| Build wheels, MCP images, or run `docker-compose.mcp.yml` | [Packaging](docs/packaging.md) · [Local wheels → images](docs/local-packages-to-images.md) |
| Understand the codebase, in order | [Reading map](docs/reading.md) |

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) for
the development setup, quality checks, and PR conventions, and our
[Code of Conduct](CODE_OF_CONDUCT.md). For questions and bug reports, see
[Support](SUPPORT.md).

## Security

To report a vulnerability, follow our [Security Policy](SECURITY.md). Do
not open a public issue for security reports.

---

## License

This project is licensed under the Apache License 2.0.
See [LICENSE](LICENSE) for details.
