<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

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

## Copyright Headers & Compliance

This repository enforces open-source licensing compliance using [REUSE](https://reuse.software/). All first-party source files must contain the appropriate SPDX copyright and license headers.

### Testing Compliance

To verify that all files have the correct headers, run the `reuse` lint tool:

```bash
uv pip install reuse
uv run reuse lint
```

### Adding Missing Headers

If `reuse lint` reports missing headers on new files, you can automatically add them by running:

```bash
bash scripts/add-license-headers.sh
```

## Dependency Inventory & Compliance

To maintain an open-source compliant ecosystem, we track all third-party dependencies and their licenses.

### License Classification Criteria
Dependencies are strictly evaluated against the following criteria:
* **✅ Safe (Permissive):** MIT, Apache-2.0, BSD, PSF. Universally safe for our Apache 2.0 release.
* **⚠️ Needs Review:** Custom or obscure licenses require manual review to ensure no conflicting obligations.
* **⛔ Risky (Copyleft):** GPLv2, GPLv3, AGPL. Strictly prohibited in the runtime application. Permitted *only* as isolated, non-distributed Development/Linting tools.

### Updating the Dependency Inventory & Security Checks
When a new package is added to the project, or before creating a release, you must run the unified compliance script.

This script will:
1. Generate the `DEPENDENCIES.md` inventory.
2. Run **Bandit** for Static Application Security Testing (SAST).
3. Run **pip-audit** for vulnerability scanning across all dependencies.

To automatically run these checks, execute:
```bash
bash scripts/run-compliance-checks.sh
```

## License

This project is licensed under the Apache License 2.0.
See the LICENSE file for details.
