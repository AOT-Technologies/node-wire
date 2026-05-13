# Node Wire

Node Wire is a three-layer Python platform that runs connector adapters (Google Drive, SMTP, Stripe, FHIR, etc.) and exposes them over REST, gRPC, or MCP. It provides a consistent execution contract with built-in validation, resilience, and telemetry.

## Quick Start

### 1. Install
```bash
git clone <repo-url>
cd node-wire
uv sync --extra agents
```
*(Requires `uv`. Alternatively, use `pip install -e ".[agents]"`)*

### 2. Configure
Copy the sample environment file and add your `NW_ALLOWED_CONNECTORS`:
```bash
# Linux/macOS/PowerShell
cp sample.env .env

# Windows (CMD)
copy sample.env .env
```
*(Edit `.env` and set `NW_ALLOWED_CONNECTORS=http_generic` or others)*

### 3. Run
**Bash (Linux/macOS):**
```bash
# Using uv (recommended)
MODE=API uv run node-wire

# Using python
MODE=API python -m bindings_entrypoint
```

**PowerShell (Windows):**
```powershell
# Using uv
$env:MODE="API"; uv run node-wire

# Using python
$env:MODE="API"; python -m bindings_entrypoint
```
*(Modes: `API`, `GRPC`, `MCP`)*

Open [http://localhost:8000/docs](http://localhost:8000/docs) to see the Swagger UI.

## Playground
The platform includes an interactive web playground at [http://localhost:8000/playground/](http://localhost:8000/playground/) (available when the REST API is running).

---

## Documentation

For more detailed information, please refer to the following guides:

- **[Architecture](docs/architecture.md)** — Layered design and data flow.
- **[Installation](docs/installation.md)** — Detailed setup and prerequisites.
- **[Configuration](docs/configuration.md)** — Environment variables and `connectors.yaml`.
- **[Connectors Guide](docs/connectors.md)** — How to use and build connectors.
- **[MCP Integration](docs/mcp.md)** — Using Node Wire with AI agents.
- **[Troubleshooting](docs/troubleshooting.md)** — Common errors and fixes.
- **[MCP Servers & Docker](docs/mcp-servers.md)** — Deploying individual connectors as MCP servers.
- **[Packaging & Publishing](docs/packaging.md)** — Wheel builds and CI flow.

   Then open:
   - **Health:** http://localhost:8000/health
   - **Swagger:** http://localhost:8000/docs

3. **Start gRPC or MCP**  
   Set `MODE=GRPC` or `MODE=MCP` before running `python -m uv run node-wire`.

---

## Dependencies

All dependencies are declared in `pyproject.toml` (Python >=3.11). They include: pydantic, FastAPI, uvicorn, tenacity, pybreaker, OpenTelemetry, grpcio, and connector-specific libraries (httpx, aiosmtplib, stripe, google-auth, google-api-python-client, etc.). See `pyproject.toml` for the full list and versions.

---

## Setup and development docs

- Platform setup (REST/gRPC/agents MCP): [Setup.md](Setup.md)
- Individual connector MCP servers (ToolHive): [docs/mcp-servers.md](docs/mcp-servers.md)
- Creating a new connector: [docs/connectors.md](docs/connectors.md)
- Quality/security gates (Bandit, SonarQube): [docs/quality-security-gates.md](docs/quality-security-gates.md)

---

## Code Quality (Linting & Formatting)

This project uses **Ruff** for linting and formatting, and **Mypy** for static type checking.

These checks are configured to run automatically in CI on Pull Requests against the `main` branch.

### Manual Usage for Developers
Make sure you have dev dependencies installed (`pip install -e ".[dev]"`).

* **Check formatting & linting errors:** `ruff check .`
* **Auto-fix everything & format code:** `ruff check --fix . && ruff format .`
* **Run static type validation:** `mypy` (paths default from `[tool.mypy]` `files` in `pyproject.toml`; avoid `mypy .`, which scans packaging `setup.py` scripts under `packages/`). To include tests: `mypy src tests` overrides the defaults.

### Pre-commit Hooks
You can attach our `.pre-commit-config.yaml` to Git so that it automatically runs these checks on every single `git commit`:
```bash
pre-commit install
```

If you ever want to force a manual test against your entire repository immediately, use:
```bash
pre-commit run --all-files
```

**(Emergency Bypass):** If the pre-commit script catches an error but you absolutely must force the commit through regardless, you can skip the checks by adding the `--no-verify` flag:
```bash
git commit -m "your message" --no-verify
```
