<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-mcp-builder

Turns a node-wire connector into a standalone MCP host under `out/`.

Auth, telemetry, and connector logic stay in node-wire. The generated project is a thin host: runtime, bindings and connector wheels, config, and env. It vendors no node-wire sources; everything is imported from the installed wheels.

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

# Tool listing mode (see below)
uv run nw-mcp-builder -c <connector_id> --tool-search
uv run nw-mcp-builder -c <connector_id> --full-tool-list
uv run nw-mcp-builder -c <connector_id> --max-tool-listing-kb 40
```

### Tool listing size and tool search

Before building wheels, the connector's MCP tool listing is measured with the bindings' own listing code. MCP clients send the whole listing to the model on every request, so when it is over the budget (**25 KB** by default, `--max-tool-listing-kb`) you are asked how the host should expose its tools:

1. **Full tool list** — every tool listed individually.
2. **Tool search** — only `nw_search_tools` + `nw_call_tool` are listed (~2 KB); the model searches by keywords and gets tool schemas on demand. The prompt explains where this can fail (a model that guesses names instead of searching, wording that doesn't match the tool, clients that approve tools one by one, an extra round trip per task).

The build never fails over the budget. Without a terminal (CI, piped output) the full list is generated and a warning names the flags. `--tool-search` / `--full-tool-list` choose up front with no prompt. The choice becomes the host's default `NW_MCP_TOOL_MODE` (in `__main__.py` and the Dockerfile `ENV`), still overridable at runtime. If the listing cannot be measured (e.g. the connector's own dependencies are not installed in this environment), the full list is used with a warning.

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
docker build -t <name>-mcp .   # the tag `nw docker-build` uses
docker run --rm --env-file .env -e NW_MCP_HOST=0.0.0.0 -p 8081:8081 <name>-mcp
```

`NW_MCP_HOST=0.0.0.0` is required in a container: the server binds loopback by default, so the published port resets connections without it. To check the running container answers and lists its tools, see [docs/cli/nw-mcp-builder.md — Verify the running container](../docs/cli/nw-mcp-builder.md#verify-the-running-container).

`<name>-mcp` / `<module_name>` come from the connector id (underscores → hyphens in the folder name, e.g. `google_drive` → `out/google-drive-nw-mcp`, module `google_drive_nw_mcp`).

### Multi-tenancy

The generated host is a thin wrapper around the same `McpServer`, so it inherits multi-tenancy for free: set `NW_MULTITENANCY_ENABLED=true` and `NW_TENANTS_PATH` (mount `tenants.yaml` into the container) in its `.env` / Docker run env. See [docs/architecture/tenancy.md](../docs/architecture/tenancy.md) for tenant resolution and the tenant/config tool walkthrough.
