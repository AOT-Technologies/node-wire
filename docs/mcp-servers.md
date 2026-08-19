<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-mcp-builder

Self-contained tool inside the **node-wire** repo that turns a node-wire connector into a standalone MCP server project.

It does **not** depend on the separate [mcp-builder](https://github.com/your-org/mcp-builder) repo. Everything needed to generate connector-mode MCP hosts lives in this folder.

</div>

---

## Platform and ToolHive (read this first)

Wheels for runtime and connectors are **Cython / platform-specific**. What you build must match how you run the host.

| Goal | Wheel platform | How to build wheels |
|------|----------------|---------------------|
| **ToolHive local MCP** (Docker image from `out/<name>-mcp`) | **Linux** (`*linux_x86_64*` / manylinux), Python **3.12** (matches `python:3.12-slim` in the generated Dockerfile) | From the **node-wire** repo root, use `scripts/build-packages.sh` (see below) |
| **Local run / MCP Inspector / ToolHive remote MCP** on the same OS as your machine | Host OS (e.g. Windows → `*win_amd64*`) | Built automatically by `uv run nw-mcp-builder -c <connector_id>` |

### Linux wheels for ToolHive Docker (local MCP server)

From the **node-wire** repository root (requires Docker; uses `python:3.12-slim` for the Linux build):

```bash
# Generic — runtime + one connector
bash scripts/build-packages.sh packages/runtime packages/connectors/<connector_id>

# Example — google_drive
bash scripts/build-packages.sh packages/runtime packages/connectors/google_drive
```

Wheels land in:

- `packages/runtime/dist/`
- `packages/connectors/<connector_id>/dist/`

Then generate (or regenerate) the host **without rebuilding host-OS wheels**, so the Linux artifacts stay selected:

```bash
cd nw-mcp-builder
uv sync
uv run nw-mcp-builder -c <connector_id> --skip-build-wheels --force-output

# Example
uv run nw-mcp-builder -c google_drive --skip-build-wheels --force-output
```

### Host-OS wheels (`nw-mcp-builder` without a prior Linux build)

```bash
uv run nw-mcp-builder -c <connector_id>
```

builds wheels for **whatever OS/Python you are on** (Windows → `win_amd64`, Linux → linux, etc.) and copies the newest `.whl` into `out/<name>-mcp/wheels/`. Those host wheels are fine for:

- running the generated host locally (`uv run python -m …`)
- **MCP Inspector**
- ToolHive **remote** MCP servers when the remote runtime matches that OS

They are **not** suitable for the generated **Linux Docker** image used as a ToolHive **local** MCP server.

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
   - Platform matches the machine running the CLI (see [Platform and ToolHive](#platform-and-toolhive-read-this-first))
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

- **Docker** — required for Linux wheels via `scripts/build-packages.sh` and for ToolHive local MCP images
- Connector secrets from node-wire `sample.env` / `config/connectors.yaml` (copied into generated `.env.example`)

---

## Quick start

### 1. Install the tool

```bash
cd nw-mcp-builder
```

From the node-wire repo root:

```bash
uv run --directory nw-mcp-builder nw-mcp-builder --help
```

### 2. Generate an MCP host

```bash
# Full run: build host-OS wheels + use/create fixture + generate project
# For Toolhive testing proceed with the second command if you alread have linux based wheel files.
# This command will be generating platform depended wheels files
uv run nw-mcp-builder -c <connector_id>
# Example
uv run nw-mcp-builder -c google_drive

# Reuse existing wheels (required after Linux build-packages.sh for ToolHive Docker)
uv run nw-mcp-builder -c <connector_id> --skip-build-wheels
# Example
uv run nw-mcp-builder -c salesforce --skip-build-wheels

# Replace an existing generated project
uv run nw-mcp-builder -c <connector_id> --force-output
# Example
uv run nw-mcp-builder -c fhir_epic --force-output

# Regenerate fixture from connector source
uv run nw-mcp-builder -c <connector_id> --force-fixture
# Example
uv run nw-mcp-builder -c slack --force-fixture
```

### 3. Run the generated host

```bash
cd out/<name>-mcp
# Example
cd out/google-drive-nw-mcp
cp .env.example .env    # optional locally; or inject secrets via env (ToolHive)
uv sync --python 3.14   # match wheel ABI (cp314 on many Windows builds)
uv run python -m <module_name>
# Example
uv run python -m google_drive_nw_mcp
```

HTTP MCP (default) listens on port **8081**. For stdio (e.g. Cursor / MCP Inspector):

```bash
NW_MCP_TRANSPORT=stdio uv run python -m <module_name>
# Example
NW_MCP_TRANSPORT=stdio uv run python -m google_drive_nw_mcp
```

#### ToolHive local MCP (Docker image from the generated host)

If you will run this as a **local MCP server in ToolHive**, build the Docker image from the generated project (after placing **Linux** wheels — see [Platform and ToolHive](#platform-and-toolhive-read-this-first)):

```bash
cd nw-mcp-builder/out/<name>-mcp
# Example
cd nw-mcp-builder/out/google-drive-nw-mcp
docker build -t <image-name>:latest .
# Example
docker build -t google-drive-nw-mcp:latest .
```

Point ToolHive at that image (e.g. `google-drive-nw-mcp:latest`).

**Secrets / env** — pick one (or combine: process env wins; project `.env` only fills unset keys):

1. **ToolHive Secrets** — set connector credentials directly (e.g. `GOOGLE_DRIVE_SA_JSON`, `GOOGLE_DRIVE_FOLDER_ID`).
2. **Volume-mounted `.env`** — copy from `.env.example` (or values from node-wire `sample.env`), fill secrets, then mount the file into the container.

**Recommended ToolHive environment variables:**

| Name | Value |
|------|--------|
| `NW_MCP_TRANSPORT` | `streamable-http` |
| `NW_MCP_HOST` | `0.0.0.0` |
| `NW_MCP_PORT` | Same as ToolHive `FASTMCP_PORT` / `MCP_PORT` (e.g. if those are `33622`, set `NW_MCP_PORT=33622`) |
| `NW_MCP_PATH` | `/mcp` |
| `NW_MCP_AUTH_DISABLED` | `true` |

Also set `NW_ALLOWED_CONNECTORS=<connector_id>` if not already baked into the image (generated Dockerfiles usually set it).

**Volume example** (optional if all secrets are in ToolHive Secrets):

| | |
|--|--|
| **Host path** | `{repo}\node-wire\nw-mcp-builder\out\<name>-mcp\.env` (use the file picker) |
| **Container path** | `/app/.env` |
| **Mode** | Read-only |

Example host path: `G:\SPACE\node-wire\nw-mcp-builder\out\google-drive-nw-mcp\.env`

---

## CLI reference

```
uv run nw-mcp-builder -c <connector_id> [options]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-c`, `--connector-id` | — (required) | node-wire connector id (`google_drive`, `salesforce`, `fhir_epic`, …) |
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

Every generated thin host prefers **process environment** (ToolHive secrets, Docker `-e`, K8s). If `out/<name>-mcp/.env` exists, it fills **unset** keys only (`override=False`). It does **not** load the node-wire monorepo or cwd `.env`. Vendored MCP/REST dotenv merge is disabled via `NW_REST_LOAD_DOTENV=false`. A missing project `.env` is OK when secrets/env are already injected.

Set these in `.env` (start from `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `NW_MCP_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `NW_MCP_PORT` | `8081` | HTTP port when using streamable-http |
| `NW_ALLOWED_CONNECTORS` | `<connector_id>` | Forced by the thin host |
| `NW_MCP_AUTH_DISABLED` | `true` | Local dev / MCP Inspector |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` | Local dev tool access |
| `NW_CONFIG_PATH` | `config/connectors.yaml` | Connector registry config |
| `NW_MULTITENANCY_ENABLED` | `false` | When `true`, load `NW_TENANTS_PATH` and expose `nw_*` tenant/config tools |
| `NW_TENANTS_PATH` | `config/tenants.yaml` | Runtime tenant/config + secret store |
| `NW_MCP_TENANT_PIN_LOCKED` | `false` | When `true`, reject `nw_select_tenant` |
| `NW_MCP_ALLOWED_TENANTS` | _(unset)_ | Comma-separated allowlist for list/select |

Connector-specific secrets (any connector) are listed in `.env.example` when auto-detected from `config/connectors.yaml` and `sample.env`. Provide them via process env (ToolHive/Docker) or copy into the **generated project** `.env` for unset keys — do not rely on the monorepo `.env`.

**Note:** MCP Inspector “Bearer token” is for MCP server auth, not upstream API credentials. Put connector secrets in process env or the generated project’s `.env`.

---

## Docker

From a generated project (use **Linux** wheels for this path — see [Platform and ToolHive](#platform-and-toolhive-read-this-first)):

```bash
cd out/<name>-mcp
docker build -t <image-name> .
docker run -p 8081:8081 --env-file .env <image-name>

# Example
cd out/salesforce-nw-mcp
docker build -t salesforce-nw-mcp .
docker run -p 8081:8081 --env-file .env salesforce-nw-mcp
```

The Dockerfile installs wheels from `./wheels`, sets `PYTHONPATH=/nw_src`, and runs `python -m <module>`.

**MCP SDK:** Pin `mcp>=1.6.0,<2`. MCP SDK 2.x breaks `@server.list_tools()` on the current server binding.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `No node-wire-runtime wheel in .../dist` | Run without `--skip-build-wheels`, or `bash scripts/build-packages.sh packages/runtime` |
| Docker / ToolHive image cannot install `.whl` | Ensure Linux (`*linux*`) wheels are in `dist/` and regenerate with `--skip-build-wheels` (Windows `win_amd64` wheels will not install in `python:3.12-slim`) |
| `Output project already exists` | Pass `--force-output` |
| `No module named node_wire_runtime.policies` | Regenerate — vendored `vendor/node_wire_src/node_wire_runtime` should be present |
| `uv sync` / import errors on generated host | Use `--python 3.14` (or whatever ABI your `.whl` files were built with) |
| Empty or wrong tools in fixture | `--force-fixture` to rescan `logic.py` |
| 503 / auth errors from MCP server | Ensure `NW_MCP_AUTH_DISABLED=true` (env or project `.env`) for local use |
| Connector secrets missing in Docker/ToolHive | Set secrets/env in the orchestrator, or mount project `.env` at `/app/.env` |
| Connector action fails at runtime | Fill connector secrets via env or project `.env`; check `config/connectors.yaml` |
| Wrong listen port in ToolHive | Set `NW_MCP_PORT` to the same value as ToolHive `FASTMCP_PORT` / `MCP_PORT` |
| `AttributeError: 'Server' object has no attribute 'list_tools'` | Pin `mcp>=1.6.0,<2` in the image Dockerfile and rebuild |
| `Config 'X' is not defined for connector 'Y'` (MT) | Use a config name that exists on every connector you call, or add it in `tenants.yaml` / REST |

---

## Multi-tenancy (MCP)

Node-wire MCP reuses the same runtime config store as REST (`NW_TENANTS_PATH` / `config/tenants.yaml`). Standalone MCP (`python -m agents.mcp_entrypoint`, ToolHive images) calls `load_tenants` on startup so named tenants/configs are available without going through the playground process.

**Enable:** `NW_MULTITENANCY_ENABLED=true`.

**Pin tenant (default):**

| Transport | How |
|-----------|-----|
| streamable-http | Client sends `X-Tenant-ID` on each request. If missing and `__default__` exists in the store, initialize uses `__default__` instead of `400 MISSING_TENANT`. |
| stdio / ToolHive container | Set `NW_TENANT_ID` for that process |

**Discover tenants:** call MCP tool `nw_list_tenants` (optional `connector_id`; legacy alias `nw.list_tenants`). Response includes `tenants`, `current_tenant_id` / `pinned_tenant_id`, and `summary`.

**Switch tenant:** call `nw_select_tenant` `{ "tenant_id": "<id>" }` (alias `nw.select_tenant`). Sets the session overlay for **every connector** on this process (stdio and streamable-http). Returns named configs. Soft pin: this overrides `NW_TENANT_ID` / `X-Tenant-ID` for later calls. Set `NW_MCP_TENANT_PIN_LOCKED=true` to reject switch. Optional `NW_MCP_ALLOWED_TENANTS` allowlist. Unknown tenants fail closed.

**Discover configs:** `nw_list_configs` (optional `connector_id` and `tenant_id`; alias `nw.list_configs`). Omit `tenant_id` to use the selected or pinned tenant. MCP does **not** create, update, or delete configs — provision those via playground Add config, REST `/v1/connectors/{cid}/configs`, or by editing `tenants.yaml`.

**Select a config:** call `nw_select_config` `{ "config_name": "<name>" }`. That name applies to every connector. Response includes `connectors_with_config` and `connectors_missing_config`. Calling a connector that lacks that name on the selected tenant returns an error. The ToolHive agent CLI `--config-name` runs `nw_select_config` at start. Connector tools do not take `tenant_id` / `config_name`.

Two ToolHive images (Drive + Epic) are two processes: select on one does not update the other. Use one MCP with both connectors (`python -m agents.mcp_entrypoint`) so one overlay covers every connector.

**Recommended ToolHive env (unified `node-wire:latest`, stdio + MT):**

| Name | Value |
|------|--------|
| `NW_MCP_TRANSPORT` | `stdio` |
| `NW_ALLOWED_CONNECTORS` | e.g. `google_drive,fhir_epic` |
| `NW_MULTITENANCY_ENABLED` | `true` |
| `NW_TENANTS_PATH` | `/app/config/tenants.yaml` |
| `NW_MCP_AUTH_DISABLED` | `true` |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` |
| `NW_MCP_TENANT_PIN_LOCKED` | `false` |

Mount host `config/tenants.yaml` → `/app/config/tenants.yaml` (read-only). Connector credentials live in that file’s `secrets:` blocks (or per-tenant env vars); flat ToolHive secrets are optional when YAML holds them.

**Cross-connector config names:** `nw_select_config` sets one name globally. If tenant `acme` has Drive config `test-drive` but Epic only `test`, Epic calls fail until you select a name that exists on **every** connector you use, or add the missing config in YAML/REST.

```text
1. Enable NW_MULTITENANCY_ENABLED=true; ensure tenants.yaml is mounted/present
2. tools/call nw_list_tenants  { "connector_id": "google_drive" }   # optional filter
3. tools/call nw_select_tenant { "tenant_id": "<id from step 2>" }  # returns configs
4. tools/call nw_select_config { "config_name": "<name from step 3>" }
5. tools/call google_drive_files_list  { ... }
```

Rebuild generated ToolHive MCP images after this change so vendored `server.py` picks up the new tools.

Verbose logging during generation:

```bash
uv run nw-mcp-builder -c <connector_id> -v
# Example
uv run nw-mcp-builder -c google_drive -v
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

Unit tests live under `tests/nw_mcp_builder/` (run from the node-wire repo root):

```bash
uv run pytest tests/nw_mcp_builder -v --no-cov
```

---

## Relationship to mcp-builder

The same connector-mode logic originated in the **mcp-builder** repo (`mcp-builder from-connector`). **nw-mcp-builder** is a minimal copy that lives inside node-wire so you can generate and run MCP hosts without checking out mcp-builder.

OpenAPI-based generation (from REST specs → custom Python MCP servers) remains in mcp-builder only.
