<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.13+ | Required to run the platform |
| `uv` or `pip` | Latest | `uv` is recommended for local development |
| Git | Any recent version | Required to clone the repository |
| Docker | Latest | Required for MCP server image builds and `docker-compose.mcp.yml` |
| Node.js | Any LTS | Only needed for MCP Inspector |

---

## Installation Steps

### 1. Clone the repository
```bash
git clone https://github.com/AOT-Technologies/node-wire.git
cd node-wire
```

### 2. Configure
Copy the sample environment file and add your `NW_ALLOWED_CONNECTORS`:
```bash
# Linux/macOS/PowerShell
cp sample.env .env

# Windows (CMD)
copy sample.env .env
```
*(Edit `.env` and set `NW_ALLOWED_CONNECTORS=http_generic` or others. Unset or empty
loads no connectors — fail-closed. Full reference:
[Configuration — Required Variables](configuration.md#required-variables).)*

The platform is single-tenant by default. To isolate callers by tenant, see [Tenancy](architecture/tenancy.md).

### 3. Install dependencies

**Using `uv` (recommended):**

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

**Using `pip` (unpinned; not recommended for reproducible builds):**
- Full install (including AI agents): `pip install -e ".[agents]"`
- Minimal install (REST/gRPC only): `pip install -e .`
- gRPC stub regeneration: `pip install -e ".[grpc-codegen]"` (optional; stubs are already committed)
- Dev tooling (ruff/mypy/pytest/bandit): there is no `dev` extra — `dev` is a `uv` `[dependency-groups]` entry, not a `pip` install extra, so `pip install -e ".[dev,agents]"` fails. Use `uv sync --frozen --extra agents --dev` for the dev toolchain, or install ruff/mypy/pytest/bandit manually if you must stay on plain `pip`.

### 4. Verify the installation
```bash
uv run python -c "from importlib.metadata import version; print('node-wire', version('node-wire'))"
```

To confirm the REST API starts, run `MODE=API uv run node-wire` and open `http://127.0.0.1:8000/health` (default bind is `127.0.0.1`; override with `NW_REST_HOST` if needed).

---

## Running the Platform

Node Wire supports REST, gRPC, and MCP entry modes:

| Mode | Command | Default port / transport | Use case |
|------|---------|--------------------------|----------|
| REST API | `uv run node-wire` | `8000` | HTTP clients, Swagger UI, playground |
| gRPC | `MODE=GRPC uv run node-wire` | `50051` | gRPC clients |
| MCP | `python -m agents.mcp_entrypoint` | `stdio` or HTTP | AI agents, ToolHive, Inspector |

### REST API

```bash
# Bash (Linux/macOS)
export NW_REST_AUTH_DISABLED=true   # local development only
MODE=API uv run node-wire           # or: MODE=API python -m bindings_entrypoint
```

```powershell
# PowerShell (Windows)
$env:NW_REST_AUTH_DISABLED="true"; $env:MODE="API"; uv run node-wire
```

Once it is running:

- Health check: `GET http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`
- Playground: `http://localhost:8000/playground/`

`MODE=MCP` is not a working server. Run MCP with `python -m agents.mcp_entrypoint` ([MCP overview](mcp.md)).

### Optional: telemetry stack (Grafana / OpenTelemetry)

To see traces and connector logs locally, start the bundled Grafana stack before the platform:

```bash
cd grafana && docker compose up -d
```

See `grafana/README.md` for what it runs.

### MCP notes

For MCP transport modes, Inspector usage, and multi-server deployment:

- See [MCP overview](mcp.md#which-mcp-path) to choose between the combined server, per-connector images and `nw gen-stacklok`.

---

## Development Setup

Lint, type-check, test and pre-commit commands are in [Code quality](code-quality-compliance.md).
Contribution rules (DCO, license headers, PRs) are in
[`CONTRIBUTING.md`](https://github.com/AOT-Technologies/node-wire/blob/main/CONTRIBUTING.md).
