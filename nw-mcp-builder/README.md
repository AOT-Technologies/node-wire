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
uv sync

# Generate (build wheels + fixture + out/<name>-mcp/)
uv run nw-mcp-builder -c <connector_id>

# Common options
uv run nw-mcp-builder -c <connector_id> --skip-build-wheels
uv run nw-mcp-builder -c <connector_id> --force-output
uv run nw-mcp-builder -c <connector_id> --force-fixture
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

`<name>-mcp` / `<module_name>` come from the connector id (underscores → hyphens in the folder name, e.g. `google_drive` → `out/google-drive-nw-mcp`, module `google_drive_nw_mcp`).
