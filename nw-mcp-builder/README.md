<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-mcp-builder

Turns a node-wire connector into a standalone MCP host under `out/`.

Auth, telemetry, and connector logic stay in node-wire. The generated project is a thin host: wheels, vendored bindings/runtime/connector sources, config, and env.

| Path | Purpose |
|------|---------|
| `src/nw_mcp_builder/` | CLI and project generator |
| `fixtures/` | Scope YAML (`<connector_id>_nw.yaml`) |
| `out/` | Generated MCP hosts |

---

## Commands

```bash
cd nw-mcp-builder

# Generate (build wheels + fixture + out/<name>-mcp/)
uv run nw-mcp-builder -c <connector_id>

# Same flow via nw-connector-builder
uv run --directory ../nw-connector-builder nw-connector-builder mcp -c <connector_id>

# Common options
uv run nw-mcp-builder -c <connector_id> --skip-build-wheels
uv run nw-mcp-builder -c <connector_id> --force-output
uv run nw-mcp-builder -c <connector_id> --force-fixture
```

### Tests

From the **node-wire** repo root (suite lives under `tests/nw_mcp_builder/`):

```bash
uv run pytest tests/nw_mcp_builder -v --no-cov
```

`<connector_id>` is any connector with `packages/connectors/<id>/` and `src/node_wire_<id>/` (e.g. `google_drive`, `salesforce`).

### Run the generated host

```bash
cd out/<name>-mcp
cp .env.example .env    # optional locally — process env / secrets win if set
uv sync                 # use a Python that matches the wheel ABI if needed
uv run python -m <module_name>
```

Default transport is HTTP on port **8081**. For stdio:

```bash
NW_MCP_TRANSPORT=stdio uv run python -m <module_name>
```

A project `.env` is local-only (never copied into Docker). For the image, pass secrets at run time:

```bash
docker build -t <module_name> .
docker run --rm --env-file .env -p 8081:8081 <module_name>
```

`<name>-mcp` / `<module_name>` come from the connector id (underscores → hyphens in the folder name, e.g. `google_drive` → `out/google-drive-nw-mcp`, module `google_drive_nw_mcp`).

### Multi-tenancy

The generated host is a thin wrapper around the same `McpServer`, so it inherits multi-tenancy for free: set `NW_MULTITENANCY_ENABLED=true` and `NW_TENANTS_PATH` (mount `tenants.yaml` into the container) in its `.env` / Docker run env. See [docs/mcp-servers.md — Multi-tenancy (MCP)](../docs/mcp-servers.md#multi-tenancy-mcp) for the full variable reference and tenant/config tool walkthrough.
