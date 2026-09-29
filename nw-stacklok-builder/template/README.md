# MCP Server Template (Python)

A production-ready template for building Python MCP (Model Context Protocol) servers using [FastMCP](https://github.com/jlowin/fastmcp).

> **⚠️ This is a template — it does not do anything useful until you edit it.**
> Out of the box it ships a single `hello` tool that echoes a greeting. You need to add your
> own tools, and those tools will almost always need a client (HTTP, SDK, DB driver, etc.)
> to talk to whatever backend they wrap. See [Implementing New Tools](#implementing-new-tools).
>
> This template is designed to run behind [ToolHive](https://github.com/stacklok/toolhive).
> Auth is delegated to ToolHive — see [Authentication](#authentication).

## What's Included

- **FastMCP server** with an example tool implementation
- **Token passthrough** — Bearer tokens from MCP clients are available to tools via context. Requests without a Bearer token are rejected with 401 by default (`REQUIRE_BEARER_TOKEN=true`); set to `false` for local development
- **Pydantic** for data validation and type safety
- **Task automation** via [Taskfile](https://taskfile.dev/) for common operations
- **Testing infrastructure** with pytest and pytest-asyncio
- **Code quality tools**: ruff (linting/formatting), ty (type checking)
- **Security scanning**: bandit, pip-audit, cyclonedx-bom (SBOM), Grype (container + filesystem)
- **Hardened containers** built on [Docker Hardened Images (DHI)](https://docs.docker.com/dhi/) — multi-arch (amd64/arm64)
- **Release pipeline** (shipped as stubs): Cosign signing, SLSA provenance attestation, GitHub Releases with auto-generated notes
- **GitHub Actions** for CI/CD, code quality, and automated builds

## Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- [Task](https://taskfile.dev/) (optional but recommended)

### Setup

```bash
# Install dependencies
task install

# Or without task:
uv sync --dev --all-packages --group security

# Copy .env (needed for `task compose` even if empty)
cp .env.example .env
```

### Run the Server

```bash
# Run locally
task run

# Or with Docker Compose (requires DHI authentication, see below)
task compose
```

The server runs on `http://0.0.0.0:8100` by default.

### DHI authentication

The Dockerfile and CI image builds use [Docker Hardened Images (DHI)](https://docs.docker.com/dhi/). DHI is **free** (Community tier, Apache 2.0) but pulls from `dhi.io` require authentication with a Docker Hub account:

```bash
# A free Docker Hub account works — no paid subscription required
docker login dhi.io
```

If you use this template for your own repo, also see [When using this template](#when-using-this-template) below for the GitHub secrets CI needs.

## Implementing New Tools

Tools are implemented in [src/mcp_template_py/api/tools.py](src/mcp_template_py/api/tools.py) as methods on the `Tools` class, and registered in [src/mcp_template_py/api/mcp_builder.py](src/mcp_template_py/api/mcp_builder.py):

**1. Define the response model in [src/mcp_template_py/api/models.py](src/mcp_template_py/api/models.py):**

```python
from pydantic import BaseModel, Field

class HelloResponse(BaseModel):
    result: str = Field(..., description="The greeting message.")
```

**2. Implement the tool in [src/mcp_template_py/api/tools.py](src/mcp_template_py/api/tools.py) with flat arguments:**

```python
class Tools:
    async def hello(self, name: str) -> HelloResponse:
        """Say hello to the user."""
        return HelloResponse(result=f"Hello, {name}!")
```

**3. Register the tool in [src/mcp_template_py/api/mcp_builder.py](src/mcp_template_py/api/mcp_builder.py):**

```python
tools = Tools()
mcp.add_tool(tools.hello)
```

**Key points:**
- Tools are async methods on the `Tools` class
- **Arguments are flat parameters** (e.g. `name: str, age: int`), not a single wrapper
  Pydantic model. MCP clients see each argument as its own field, not a nested `request`
  object
- **Return a Pydantic model** so the tool has an explicit, typed output schema
- Docstrings become tool descriptions for MCP clients; use `Annotated[..., Field(description=...)]`
  on a parameter if you want per-argument descriptions in the schema
- Tools are registered via `mcp.add_tool()` in the MCP builder
- Use `get_bearer_token()` from `mcp_template_py.auth` to access the client's Bearer token

## Authentication

This template does not implement its own auth server. It expects to run behind
[ToolHive](https://github.com/stacklok/toolhive), which handles identity, token
validation, and policy. To get auth working, configure the auth server in ToolHive — see
the [ToolHive authentication docs](https://docs.stacklok.com/toolhive/concepts/auth-framework).

What this server does is **passthrough**: it takes the `Authorization: Bearer <token>`
header that ToolHive forwards and makes the token available to your tools so they can
forward it to whatever upstream API they call.

```python
from mcp_template_py.auth import get_bearer_token

token = get_bearer_token()  # token forwarded by ToolHive
```

For local development without ToolHive, set `REQUIRE_BEARER_TOKEN=false` to skip the
401-on-missing-token check.

## Task Commands

| Command | Description |
|---------|-------------|
| `task install` | Install all dependencies (dev + security) |
| `task run` | Run the MCP server locally |
| `task compose` | Start server with Docker Compose |
| `task lint` | Run ruff linter |
| `task format` | Format code and fix lint issues |
| `task typecheck` | Run ty type checker |
| `task test` | Run pytest tests |
| `task security` | Run bandit + pip-audit |
| `task sbom` | Generate a CycloneDX SBOM |
| `task check` | Run all checks (lint, typecheck, test, security) |

## Project Structure

```
src/mcp_template_py/
├── __main__.py              # Server entry point
├── settings.py              # Configuration and environment variables
├── configure_logging.py     # Logging configuration
├── api/
│   ├── app_builder.py       # Starlette application factory
│   ├── mcp_builder.py       # FastMCP server builder
│   ├── tools.py             # MCP tool implementations
│   └── models.py            # Pydantic models for tools
└── auth/
    └── mcp_auth_middleware.py  # Token passthrough middleware

tests/
├── unit/                    # Unit tests with mocked dependencies
│   ├── test_settings.py     # Settings validation tests
│   └── test_token_passthrough.py  # Middleware tests
└── integration/             # Integration tests (requires running server)
    └── test_mcp.py          # MCP tool tests
```

## Configuration

Configuration is managed through environment variables. Copy `.env.example` to `.env` and configure as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `false` | Enable debug logging |
| `MCP_HOST` | `0.0.0.0` | Host for the MCP server to listen on |
| `MCP_PORT` | `8100` | Port for the MCP server to listen on |
| `SERVER_URL` | `http://localhost:8100` | Base URL of the server |
| `REQUIRE_BEARER_TOKEN` | `true` | Reject requests without a Bearer token |

## Testing

Integration tests (`tests/integration/`) require the MCP server to be running (`task run` or `task compose`) and will be skipped if unreachable.

```bash
# Run all tests
task test

# Run with coverage
uv run pytest --cov=src/mcp_template_py
```

## When Using This Template

Whether you created a new repo via "Use this template" or actually forked this one, CI needs a few GitHub Actions secrets to work in your copy:

**Required for image builds and security scans** (`image-build.yml`, `security.yml`):
- `DOCKERHUB_USERNAME` — your Docker Hub username
- `DOCKERHUB_TOKEN` — a [Docker Hub access token](https://docs.docker.com/security/for-developers/access-tokens/) with public-read scope

**Required when you enable the release pipeline** (see [docs/release-playbook.md](docs/release-playbook.md)):
- `MCP_RELEASE_WORKFLOW_APP_ID` — numeric App ID of a GitHub App installed on your repo with Contents: Read/Write
- `MCP_RELEASE_WORKFLOW_APP_KEY` — the App's private key (full `.pem` contents)

The release workflows (`release.yml`, `create-release.yml`, `patch-release.yml`) ship **stubbed** so the template itself does not publish artifacts. To enable them in your copy, follow the unstub steps in [docs/release-playbook.md](docs/release-playbook.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, PR conventions, and the DCO sign-off requirement.

## Security

To report a vulnerability, please use the GitHub Security Advisory flow described in [SECURITY.md](SECURITY.md) — do not file a public issue.

## License

[Apache 2.0](LICENSE).
