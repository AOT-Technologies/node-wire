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

