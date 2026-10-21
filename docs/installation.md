<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Installation guide

## Prerequisites

You need the following tools:

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11, 3.12 or 3.13 | Required to run the platform |
| `uv` or `pip` | Latest | `uv` is recommended for local development |
| Git | Any recent version | Required to clone the repository |
| Docker | Latest | Required for MCP server image builds and `docker-compose.mcp.yml` (wheel builds don't need it) |
| Node.js | Any LTS | Only needed for MCP Inspector |
| Claude Code | Latest | Only needed for `nw gen-stacklok --path` (Phase 1 AI scoping); `claude` on `PATH`. See [stacklok MCP servers](stacklok-mcp-servers.md) |

---

## Installation steps

### Clone the repository

Clone the repository and change into it:

```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
```

### Configure

Copy the sample environment file and add your `NW_ALLOWED_CONNECTORS`:

```bash
# Linux/macOS/PowerShell
cp sample.env .env

# Windows (CMD)
copy sample.env .env
```

Edit `.env` and set `NW_ALLOWED_CONNECTORS=http_generic` or others. Unset or empty
loads no connectors — fail-closed. Full reference:
[Configuration — Required variables](configuration.md#required-variables).

The platform is single-tenant by default. To isolate callers by tenant, see [Tenancy](architecture/tenancy.md).

### Install dependencies

#### Using `uv` (recommended)

The repository commits `uv.lock` for reproducible installs. Use `--frozen` in CI and local dev:

```bash
uv sync --frozen --extra agents --dev   # full dev + agents (matches CI)
uv sync --frozen --no-dev               # runtime only
# Regenerating gRPC stubs only (scripts/generate-grpc-stubs.sh):
# uv sync --frozen --extra grpc-codegen
```

Plain `uv sync --frozen` (no `--no-dev`) still installs the `dev` dependency group — `pyproject.toml` sets `default-groups = ["dev"]` — so it is **not** a runtime-only install on its own.

When you change dependencies in `pyproject.toml`, regenerate and commit the lockfile:

```bash
uv lock
```

#### Using `pip` (unpinned; not recommended for reproducible builds)

Choose the install that matches what you need:

- Full install (including AI agents): `pip install -e ".[agents]"`
- Minimal install (REST/gRPC only): `pip install -e .`
- gRPC stub regeneration: `pip install -e ".[grpc-codegen]"` (optional; stubs are already committed)
- Dev tooling (ruff/mypy/pytest/bandit): there is no `dev` extra — `dev` is a `uv` `[dependency-groups]` entry, not a `pip` install extra, so `pip install -e ".[dev,agents]"` fails. Use `uv sync --frozen --extra agents --dev` for the dev toolchain, or install ruff/mypy/pytest/bandit manually if you must stay on plain `pip`.

### Verify the installation

Print the installed version:

```bash
uv run python -c "from importlib.metadata import version; print('node-wire', version('node-wire'))"
```

To confirm the REST API starts, run `MODE=API uv run node-wire` and open `http://127.0.0.1:8000/health` (default bind is `127.0.0.1`; override with `NW_REST_HOST` if needed).

---

## Running the platform

Node Wire supports REST, gRPC, and MCP entry modes:

| Mode | Command | Default port / transport | Use case |
|------|---------|--------------------------|----------|
| REST API | `uv run node-wire` | `8000` | HTTP clients, Swagger UI, playground |
| gRPC | `MODE=GRPC uv run node-wire` | `50051` | gRPC clients |
| MCP | `python -m agents.mcp_entrypoint` | `stdio` or HTTP | AI agents, ToolHive, Inspector |

### REST API

To start the REST API on Linux or macOS, run:

```bash
# Bash (Linux/macOS)
export NW_REST_AUTH_DISABLED=true        # local development only
export NW_MCP_SCOPE_POLICY_DEFAULT=allow  # .env sets deny; with auth off there is no caller scope
MODE=API uv run node-wire           # or: MODE=API python -m bindings_entrypoint
```

On Windows, run the following in PowerShell:

```powershell
# PowerShell (Windows)
$env:NW_REST_AUTH_DISABLED="true"; $env:NW_MCP_SCOPE_POLICY_DEFAULT="allow"; $env:MODE="API"; uv run node-wire
```

Once it is running:

- Health check: `GET http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`
- Playground: `http://localhost:8000/playground/`
- Call an action: `POST http://localhost:8000/connectors/<id>/<action>` with the action's fields as the JSON body (the connector must be in `NW_ALLOWED_CONNECTORS` in `.env`). Keeping auth on instead? Use an API key with scopes, see [Build a connector — Security](connectors-build.md#security-rest-plugins-secrets).

### MCP

`MODE=MCP` is not a working server. Run MCP with `python -m agents.mcp_entrypoint`. To choose between the combined server, per-connector images and `nw gen-stacklok`, and for transport modes and Inspector usage, see the [MCP overview](mcp.md#which-mcp-path).

### Optional: telemetry stack (Grafana / OpenTelemetry)

To see traces and connector logs locally, start the bundled Grafana stack before the platform:

```bash
cd grafana && docker compose up -d
```

See `grafana/README.md` for what it runs.

---

## Development setup

Lint, type-check, test and pre-commit commands are in [Code quality](code-quality-compliance.md).
The `.env` you created in [Configure](#configure) affects `pytest`. Move it aside before you run the
suite, as [Code quality — Tests](code-quality-compliance.md#tests) explains.
Contribution rules (DCO, license headers, PRs) are in
[`CONTRIBUTING.md`](https://github.com/AOT-Technologies/node-wire/blob/main/CONTRIBUTING.md).
