<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | Required to run the platform |
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
*(Edit `.env` and set `NW_ALLOWED_CONNECTORS=http_generic` or others)*

Node Wire uses a fail-closed connector allowlist. If `NW_ALLOWED_CONNECTORS` is missing or empty, no connectors are loaded even when they are enabled in `config/connectors.yaml`.

### 3. Install dependencies

**Using `uv` (recommended):**

The repository commits `uv.lock` for reproducible installs. Use `--frozen` in CI and local dev:

```bash
uv sync --frozen --all-extras --dev   # full dev + agents (matches CI)
uv sync --frozen                      # runtime only
```

When you change dependencies in `pyproject.toml`, regenerate and commit the lockfile:

```bash
uv lock
```

**Using `pip` (unpinned; not recommended for reproducible builds):**
- Full install (including AI agents): `pip install -e ".[agents]"`
- Minimal install (REST/gRPC only): `pip install -e .`
- Dev install (linting/tests): `pip install -e ".[dev,agents]"`

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

### REST quick start

```bash
# Local development only
export NW_REST_AUTH_DISABLED=true

# Start the API
uv run node-wire
```

Once it is running:

- Health check: `GET http://localhost:8000/health`
- Swagger UI: `http://localhost:8000/docs`
- Playground: `http://localhost:8000/playground/`

### MCP notes

For MCP transport modes, Inspector usage, and multi-server deployment:

- See [mcp.md](mcp.md) for transport setup and local MCP usage.
- See [mcp-servers.md](mcp-servers.md) for per-connector images, ToolHive, and Docker-based MCP deployment.

---

## Development Setup

### Code Quality (Linting & Formatting)
We use **Ruff** for linting/formatting and **Mypy** for type checking.

- **Check:** `ruff check .`
- **Fix:** `ruff check --fix . && ruff format .`
- **Types:** `mypy`

`mypy` defaults to the `[tool.mypy].files` targets from `pyproject.toml`. To include tests explicitly, run `mypy src tests`.

### Pre-commit Hooks
```bash
pre-commit install
```

### Running Tests
```bash
pytest tests/ -v
```
Integration tests are skipped unless the relevant environment variables (secrets) are set.
