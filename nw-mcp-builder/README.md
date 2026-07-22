<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-mcp-builder

Self-contained tool inside the **node-wire** repo that turns a node-wire connector into a standalone MCP server project.

It does **not** depend on the separate [mcp-builder](https://github.com/your-org/mcp-builder) repo. Everything needed to generate connector-mode MCP hosts lives in this folder.


---

## What this folder is

| Path | Purpose |
|------|---------|
| `src/nw_mcp_builder/` | Python package: CLI, fixture writer, project generator |
| `fixtures/` | Connector-mode scope YAML files (`<connector_id>_nw.yaml`) |
| `out/` | Generated MCP host projects (one folder per connector) |

Generated hosts are **thin wrappers** around node-wire’s `McpServer`. Auth, telemetry, and connector logic stay in node-wire — the host only wires wheels, config, and env.

---

## What it does (end to end)

For a connector id like `google_drive` or `salesforce`, `nw-mcp-builder`:

1. **Validates** the connector exists under `packages/connectors/<id>` and `src/node_wire_<id>/logic.py`
2. **Builds wheels** (unless `--skip-build-wheels`):
   - `packages/runtime/dist/node_wire_runtime-*.whl`
   - `packages/connectors/<id>/dist/node_wire_<id>-*.whl`
3. **Ensures a scope fixture** at `fixtures/<id>_nw.yaml`  
   - Auto-generated from `@nw_action` / `@sdk_action` / `SdkActionSpec` in connector source  
   - Skips overwrite if the file already exists (use `--force-fixture` to regenerate)
4. **Generates** `out/<server-name>-mcp/` containing:
   - Copied wheels under `wheels/`
   - Selective vendored `node-wire/src` → `vendor/node_wire_src` (`bindings`, `node_wire_runtime`, `node_wire_<connector_id>` only; Docker PYTHONPATH parity)
   - `config/connectors.yaml` from the monorepo
   - Thin `__main__.py` that runs `McpServer(connector_ids=[...])`
   - `pyproject.toml`, `README.md`, `Dockerfile`, `.env.example`

Example mapping:

| `connector_id` | Generated folder | Python module |
|----------------|------------------|---------------|
| `google_drive` | `out/google-drive-nw-mcp/` | `google_drive_nw_mcp` |
| `salesforce` | `out/salesforce-nw-mcp/` | `salesforce_nw_mcp` |
| `fhir_epic` | `out/fhir-epic-nw-mcp/` | `fhir_epic_nw_mcp` |

Server name in the fixture is always `{connector_id with _ → -}-nw` (e.g. `google-drive-nw`).

---

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — package manager and runner
- **Python 3.11+** for `nw-mcp-builder` itself
- **Python version matching wheels** for generated hosts — on Windows, wheels are often built as **cp314**, so use:

  ```bash
  uv sync --python 3.14
  ```

- Connector secrets from node-wire `sample.env` / `config/connectors.yaml` (copied into generated `.env.example`)

---

## Quick start

### 1. Install the tool

```bash
cd nw-mcp-builder
uv sync
```

From the node-wire repo root:

```bash
uv run --directory nw-mcp-builder nw-mcp-builder --help
```

### 2. Generate an MCP host

```bash
# Full run: build wheels + use/create fixture + generate project
uv run nw-mcp-builder google_drive

# Reuse existing wheels (faster if you already built them)
uv run nw-mcp-builder salesforce --skip-build-wheels

# Replace an existing generated project
uv run nw-mcp-builder fhir_epic --force-output

# Regenerate fixture from connector source
uv run nw-mcp-builder slack --force-fixture
```

### 3. Run the generated host

```bash
cd out/google-drive-nw-mcp
cp .env.example .env    # fill connector secrets (see node-wire sample.env)
uv sync --python 3.14   # match wheel ABI (cp314 on many Windows builds)
uv run python -m google_drive_nw_mcp
```

HTTP MCP (default) listens on port **8081**. For stdio (e.g. Cursor / MCP Inspector):

```bash
NW_MCP_TRANSPORT=stdio uv run python -m google_drive_nw_mcp
```

---

## CLI reference

```
uv run nw-mcp-builder <connector_id> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `connector_id` | — | node-wire connector id (`google_drive`, `salesforce`, `fhir_epic`, …) |
| `--node-wire-root` | Parent of `nw-mcp-builder/` | node-wire monorepo root |
| `-o`, `--output-dir` | `nw-mcp-builder/out` | Where generated hosts are written |
| `--fixtures-dir` | `nw-mcp-builder/fixtures` | Where `*_nw.yaml` fixtures live |
| `--skip-build-wheels` | off | Use wheels already in `packages/*/dist` |
| `--force-fixture` | off | Overwrite `fixtures/<id>_nw.yaml` from connector source |
| `--force-output` | off | Delete and regenerate `out/<server>-mcp/` if it exists |
| `--python` | — | Python for wheel builds (sets `UV_PYTHON`) |
| `-v`, `--verbose` | off | Debug logging |

Help:

```bash
uv run nw-mcp-builder --help
```

---

## Supported connectors

Any connector with this layout in the node-wire repo:

```
packages/connectors/<connector_id>/pyproject.toml
src/node_wire_<connector_id>/logic.py
```

Currently in this monorepo:

- `google_drive`
- `salesforce`
- `fhir_epic`
- `fhir_cerner`
- `slack`
- `stripe`
- `smtp`
- `http_generic`

---

## Fixtures (`fixtures/`)

Scope YAML files describe the MCP server metadata for connector mode. They are **not** used to codegen HTTP clients — node-wire dispatches tools at runtime from the connector wheel.

- **Auto-generated** — scans `logic.py` (and sibling `.py` files) for action names when the fixture is missing or `--force-fixture` is set
- **Hand-maintained** — files like `fhir_epic_nw.yaml` can include richer tool descriptions; existing fixtures are kept unless you pass `--force-fixture`

Minimal shape:

```yaml
version: "1"
server:
  name: salesforce-nw
  description: ...
runtime:
  type: node_wire
  connector_id: salesforce
spec:
  source: node-wire/src/node_wire_salesforce
  format: openapi3
  base_url: https://unused.invalid
groups:
  - name: connector
    tools: [...]
auth:
  type: none
```

---

## Generated project layout

Each `out/<name>-mcp/` folder is a deployable MCP host:

```
out/google-drive-nw-mcp/
  pyproject.toml          # deps from local wheels
  Dockerfile
  README.md
  .env.example            # NW + connector secret env names
  wheels/                 # runtime + connector .whl
  config/connectors.yaml
  vendor/node_wire_src/   # bindings + node_wire_runtime + node_wire_<id> only
  src/google_drive_nw_mcp/
    __main__.py           # McpServer entrypoint
```

### Environment variables (generated host)

Set these in `.env` (start from `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `NW_MCP_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `NW_MCP_PORT` | `8081` | HTTP port when using streamable-http |
| `NW_ALLOWED_CONNECTORS` | `<connector_id>` | Forced by the thin host |
| `NW_MCP_AUTH_DISABLED` | `true` | Local dev / MCP Inspector |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` | Local dev tool access |
| `NW_CONFIG_PATH` | `config/connectors.yaml` | Connector registry config |

Connector-specific secrets (e.g. `SALESFORCE_CLIENT_ID`, `GOOGLE_DRIVE_SA_JSON`) are listed in `.env.example` when auto-detected from `config/connectors.yaml` and `sample.env`. Copy values from the node-wire repo `.env` or `sample.env` as needed.

**Note:** MCP Inspector “Bearer token” is for MCP server auth, not Salesforce/Google API credentials. Put API secrets in the generated project’s `.env`.

---

## Docker

From a generated project:

```bash
cd out/salesforce-nw-mcp
docker build -t salesforce-nw-mcp .
docker run -p 8081:8081 --env-file .env salesforce-nw-mcp
```

The Dockerfile installs wheels from `./wheels`, sets `PYTHONPATH=/nw_src`, and runs `python -m <module>`.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `No node-wire-runtime wheel in .../dist` | Run without `--skip-build-wheels`, or build manually in `packages/runtime` |
| `Output project already exists` | Pass `--force-output` |
| `No module named node_wire_runtime.policies` | Regenerate — vendored `vendor/node_wire_src/node_wire_runtime` should be present |
| `uv sync` / import errors on generated host | Use `--python 3.14` (or whatever ABI your `.whl` files were built with) |
| Empty or wrong tools in fixture | `--force-fixture` to rescan `logic.py` |
| 503 / auth errors from MCP server | Ensure `.env` has `NW_MCP_AUTH_DISABLED=true` for local use |
| Connector action fails at runtime | Fill connector secrets in `.env`; check `config/connectors.yaml` in node-wire |

Verbose logging during generation:

```bash
uv run nw-mcp-builder google_drive -v
```

---

## Package layout (source)

```
nw-mcp-builder/
  pyproject.toml
  README.md
  fixtures/                 # *_nw.yaml scope files
  out/                      # generated MCP hosts (gitignored content typical)
  src/nw_mcp_builder/
    cli.py                  # entrypoint: nw-mcp-builder
    from_connector.py       # wheels → fixture → pipeline → .env.example
    pipeline.py             # run_connector_pipeline
    generate/
      connector_project.py  # emit out/<server>-mcp/
    schema/
      models.py             # MCPScope validation for fixtures
```

Dependencies: `pydantic`, `pyyaml` only (no mcp-builder dependency).

---

## Relationship to mcp-builder

The same connector-mode logic originated in the **mcp-builder** repo (`mcp-builder from-connector`). **nw-mcp-builder** is a minimal copy that lives inside node-wire so you can generate and run MCP hosts without checking out mcp-builder.

OpenAPI-based generation (from REST specs → custom Python MCP servers) remains in mcp-builder only.
