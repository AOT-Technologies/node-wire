<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-mcp-builder

> [!NOTE]
> **Advanced — most users want the `nw` CLI.** `nw-mcp-builder` is the standalone entry point and
> exposes the full flag surface. For the normal path (spec → connector → wheels → MCP host → image)
> use [`nw gen-all`](nw-cli.md), which calls this tool for you. Read on when you need a flag `nw`
> does not pass through, or are debugging this stage on its own.

Self-contained tool inside the **node-wire** repo that turns a node-wire connector into a standalone MCP server project.

It does **not** depend on any separate external `mcp-builder` project. Everything needed to generate connector-mode MCP hosts lives in this folder.

To **create** a connector from OpenAPI/Swagger first, use [nw-connector-builder](nw-connector-builder.md) (it can hand off to this tool automatically unless you pass `--no-mcp`), or the full pipeline via [`nw gen-all`](nw-cli.md).

You can also run MCP generation as a subcommand of the connector builder (same flags), or via the orchestrator CLI:

```bash
uv run --directory nw-connector-builder nw-connector-builder mcp -c <connector_id>
# equivalent happy-path stages:
uv run nw gen-mcp --connector-id <connector_id>
```

Replace `<connector_id>` with the connector's id, as in `config/connectors.yaml` (for example, `google_drive`).

The standalone `nw-mcp-builder` entry point remains supported.

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
   - `packages/bindings/dist/node_wire_bindings-*.whl`
   - `packages/connectors/<id>/dist/node_wire_<id>-*.whl`
   - Pure-Python wheels that install anywhere (see [Wheels](#wheels))
3. **Ensures a scope fixture** at `fixtures/<id>_nw.yaml`
   - Auto-generated from the actions the connector class registers (`nw_action_metas()` on the class in `logic.py`)
   - Skips overwrite if the file already exists (use `--force-fixture` to regenerate)
4. **Generates** `out/<server-name>-mcp/` containing:
   - Copied wheels under `wheels/` (runtime, bindings, connector). The host imports everything from these installed wheels; no node-wire source tree is vendored into it
   - `config/connectors.yaml` from the monorepo
   - Thin `__main__.py` that runs `McpServer(connector_ids=[...])`
   - `pyproject.toml`, `README.md`, `Dockerfile`, `.env.example`

Example mapping:

| `connector_id` | Generated folder | Python module |
|----------------|------------------|---------------|
| `google_drive` | `out/google-drive-nw-mcp/` | `google_drive_nw_mcp` |
| `salesforce` | `out/salesforce-nw-mcp/` | `salesforce_nw_mcp` |
| `fhir_epic` | `out/fhir-epic-nw-mcp/` | `fhir_epic_nw_mcp` |

Server name in the fixture is always `{connector_id with _ → -}-nw` (for example, `google-drive-nw`).

---

## Requirements

- **[uv](https://docs.astral.sh/uv/)** — package manager and runner
- **Python 3.11, 3.12 or 3.13** for `nw-mcp-builder` itself
- **Python version matching wheels** for generated hosts — the generated host must run the same
  Python minor version the wheels were built for. Generated hosts target **3.13** (the
  generated Dockerfile uses `python:3.13-slim`). If your host wheels were built by a different
  interpreter, pin it explicitly:

  ```bash
  uv sync --python 3.13
  ```

- **Docker** — required for ToolHive local MCP images (not for wheel builds)
- Connector secrets from node-wire `sample.env` / `config/connectors.yaml` (copied into generated `.env.example`)

---

## Wheels

The runtime, bindings and connector wheels are pure Python (`py3-none-any`). One build installs on your machine, in the generated Linux Docker image (ToolHive local MCP), and on any remote runtime, so there is no platform to match.

```bash
uv run nw-mcp-builder -c <connector_id>
```

This builds the connector wheel and copies the newest pure-Python wheels into `out/<name>-mcp/wheels/`. To use wheels you already built from the **node-wire** repository root instead:

```bash
bash scripts/build-packages.sh packages/runtime packages/bindings packages/connectors/<connector_id>

cd nw-mcp-builder
uv sync
uv run nw-mcp-builder -c <connector_id> --skip-build-wheels --force-output
```

Wheels land in each package's `dist/` (for example `packages/runtime/dist/`). A compiled wheel left in `dist/` from an older build is skipped; rebuild the package if that leaves none.

---

## Quick start

### Install the tool

Change into the tool's directory:

```bash
cd nw-mcp-builder
```

From the node-wire repo root:

```bash
uv run --directory nw-mcp-builder nw-mcp-builder --help
```

### Generate an MCP host

The following commands cover the common generation modes:

```bash
# Full run: build wheels + use/create fixture + generate project
uv run nw-mcp-builder -c <connector_id>
# Example
uv run nw-mcp-builder -c google_drive

# Reuse wheels already built (e.g. by scripts/build-packages.sh or nw gen-whl)
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

### Run the generated host

Change into the generated project, install it, and run it:

```bash
cd out/<name>-mcp
# Example
cd out/google-drive-nw-mcp
cp .env.example .env    # optional locally; or inject secrets via env (ToolHive)
uv sync --python 3.13   # must match the ABI your wheels were built for
uv run python -m <module_name>
# Example
uv run python -m google_drive_nw_mcp
```

Replace the following:

- `<name>`: the connector id with `_` replaced by `-`, plus `-nw` (for example, `google-drive-nw` for `google_drive`).
- `<module_name>`: the same name with `-` replaced by `_`, plus `_mcp` (for example, `google_drive_nw_mcp`).

HTTP MCP (default) listens on port **8081**. For stdio (for example, Cursor / MCP Inspector):

```bash
NW_MCP_TRANSPORT=stdio uv run python -m <module_name>
# Example
NW_MCP_TRANSPORT=stdio uv run python -m google_drive_nw_mcp
```

#### ToolHive local MCP (Docker image from the generated host)

If you will run this as a **local MCP server in ToolHive**, build the Docker image from the generated project:

```bash
cd nw-mcp-builder/out/<name>-mcp
# Example
cd nw-mcp-builder/out/google-drive-nw-mcp
docker build -t <image-name>:latest .
# Example
docker build -t google-drive-nw-mcp:latest .
```

Replace `<name>` as in [Run the generated host](#run-the-generated-host), and `<image-name>` with any image name you choose.

Point ToolHive at that image (for example, `google-drive-nw-mcp:latest`).

**Secrets / env** — pick one (or combine: process env wins; project `.env` only fills unset keys):

- **ToolHive Secrets** — set connector credentials directly (for example, `GOOGLE_DRIVE_SA_JSON`, `GOOGLE_DRIVE_FOLDER_ID`).
- **Volume-mounted `.env`** — copy from `.env.example` (or values from node-wire `sample.env`), fill secrets, then mount the file into the container.

**Recommended ToolHive environment variables:**

| Name | Value |
|------|--------|
| `NW_MCP_TRANSPORT` | `streamable-http` |
| `NW_MCP_HOST` | `0.0.0.0` |
| `NW_MCP_PORT` | Same as ToolHive `FASTMCP_PORT` / `MCP_PORT` (for example, if those are `33622`, set `NW_MCP_PORT=33622`) |
| `NW_MCP_PATH` | `/mcp` |
| `NW_MCP_AUTH_DISABLED` | `true` |

Also set `NW_ALLOWED_CONNECTORS=<connector_id>` if not already baked into the image (generated Dockerfiles usually set it).

**Volume example** (optional if all secrets are in ToolHive Secrets):

| Setting | Value |
|--|--|
| **Host path** | `<repo>\node-wire\nw-mcp-builder\out\<name>-mcp\.env` (use the file picker) |
| **Container path** | `/app/.env` |
| **Mode** | Read-only |

Example host path: `G:\SPACE\node-wire\nw-mcp-builder\out\google-drive-nw-mcp\.env`

---

## CLI reference

The command takes the following form:

```bash
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
| `--tool-search` | off | Serve tools through `nw_search_tools` + `nw_call_tool` (for large connectors). Mutually exclusive with `--full-tool-list` |
| `--full-tool-list` | off | List every tool, even over the size budget (no prompt, no warning) |
| `--max-tool-listing-kb` | `25` | Tool listing budget in KB. Over it, an interactive run asks you to choose full list or tool search; without a terminal the full list is used with a warning |
| `-v`, `--verbose` | off | Debug logging |

To print help, or log verbosely during generation:

```bash
uv run nw-mcp-builder --help
uv run nw-mcp-builder -c <connector_id> -v   # verbose logging during generation
```

---

## Supported connectors

Any connector with this layout in the node-wire repo:

```text
packages/connectors/<connector_id>/pyproject.toml
src/node_wire_<connector_id>/logic.py
```

The monorepo ships the following connectors:

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

- **Auto-generated** — built from the connector's registered actions when the fixture is missing or `--force-fixture` is set
- **Hand-maintained** — you can edit a fixture to add richer tool descriptions; an existing fixture is kept unless you pass `--force-fixture`

Shape (abridged; the generated file also carries `workflows` and per-tool `endpoint`, `response_kind` and `hints`):

```yaml
version: "1"
server:
  name: salesforce-nw
  description: <description>
runtime:
  type: node_wire
  connector_id: salesforce
spec:
  source: node-wire/src/node_wire_salesforce
  format: openapi3
  base_url: https://unused.invalid
groups:
  - name: connector
    description: <group-description>
    tools:
      - tool_name: <tool-name>
        endpoint: POST /connectors/salesforce/<action>
        description: <tool-description>
        response_kind: json
auth:
  type: none
```

---

## Generated project layout

Each `out/<name>-mcp/` folder is a deployable MCP host:

```text
out/google-drive-nw-mcp/
  pyproject.toml          # deps from local wheels
  Dockerfile
  README.md
  .env.example            # NW + connector secret env names
  .dockerignore           # allowlist: wheels/, config/connectors.yaml, src/
  wheels/                 # runtime + bindings + connector .whl
  config/connectors.yaml
  src/google_drive_nw_mcp/
    __init__.py
    __main__.py           # McpServer entrypoint
```

### Environment variables (generated host)

Every generated thin host prefers **process environment** (ToolHive secrets, Docker `-e`, K8s). If `out/<name>-mcp/.env` exists, it fills **unset** keys only (`override=False`). It does **not** load the node-wire monorepo or cwd `.env`. The bindings' own MCP/REST dotenv merge is disabled via `NW_REST_LOAD_DOTENV=false`. A missing project `.env` is OK when secrets/env are already injected.

Set these in `.env` (start from `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `NW_MCP_TRANSPORT` | `streamable-http` | `stdio` or `streamable-http` |
| `NW_MCP_PORT` | `8081` | HTTP port when using streamable-http |
| `NW_MCP_HOST` | loopback (see [Configuration](../configuration.md#transport-binding-config)) | Bind address for streamable-http. Set `0.0.0.0` inside a container, otherwise the published port answers with `Connection reset by peer` |
| `NW_ALLOWED_CONNECTORS` | `<connector_id>` | Forced by the thin host |
| `NW_MCP_TOOL_MODE` | chosen at generation (`list` or `search`) | `list` shows every tool; `search` lists only `nw_search_tools` + `nw_call_tool` |
| `NW_MCP_AUTH_DISABLED` | `true` locally; unset in images | Local dev / MCP Inspector |
| `NW_MCP_SCOPE_POLICY_DEFAULT` | `allow` locally; unset (`deny`) in images | Local dev tool access |
| `NW_CONFIG_PATH` | `config/connectors.yaml` | Connector registry config |
| `NW_MULTITENANCY_ENABLED` | `false` | When `true`, load `NW_TENANTS_PATH` and expose `nw_*` tenant/config tools |
| `NW_TENANTS_PATH` | `config/tenants.yaml` | Runtime tenant/config + secret store |
| `NW_MCP_TENANT_PIN_LOCKED` | `false` | When `true`, reject `nw_select_tenant` |
| `NW_MCP_ALLOWED_TENANTS` | _(unset)_ | Comma-separated allowlist for list/select |

Connector-specific secrets (any connector) are listed in `.env.example` when auto-detected from `config/connectors.yaml` and `sample.env`. Provide them via process env (ToolHive/Docker) or copy into the **generated project** `.env` for unset keys — do not rely on the monorepo `.env`.

MCP Inspector's “Bearer token” is for MCP server auth, not upstream API credentials. Put connector secrets in process env or the generated project’s `.env`.

---

## Docker

Build and run a generated project from the **node-wire** repo root. `nw docker-build --connector-id <id>` runs the same build and tags `<id-dashed>-nw-mcp:latest` (see [nw CLI](nw-cli.md#nw-docker-build)):

```bash
cd nw-mcp-builder/out/<name>-mcp
docker build -t <image-name> .
cp .env.example .env   # fill connector secrets; keeps NW_MCP_AUTH_DISABLED / NW_MCP_SCOPE_POLICY_DEFAULT for local use
# Secrets at runtime only — never COPY .env into the image.
# NW_MCP_HOST=0.0.0.0 is required: the server binds loopback by default, which is unreachable through -p.
docker run --rm --env-file .env -e NW_MCP_HOST=0.0.0.0 -p 8081:8081 <image-name>

# Example (generated by `nw gen-all --connector-id petstore_review`)
cd nw-mcp-builder/out/petstore-review-nw-mcp
cp .env.example .env
docker run --rm --env-file .env -e NW_MCP_HOST=0.0.0.0 -p 8081:8081 petstore-review-nw-mcp:latest
```

The startup log must say `Uvicorn running on http://0.0.0.0:8081`. If it says `127.0.0.1`, `NW_MCP_HOST` was not set and requests from the host get `Connection reset by peer`.

### Verify the running container

From a second terminal, open an MCP session and list the tools. Streamable HTTP needs both `Accept` types, and every request after `initialize` must echo the `Mcp-Session-Id` response header:

```bash
URL=http://localhost:8081/mcp
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')

SID=$(curl -s -D - -o /dev/null "$URL" "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  | awk 'tolower($1)=="mcp-session-id:" {print $2}' | tr -d '\r')
echo "session: $SID"

curl -s "$URL" "${H[@]}" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' -o /dev/null -w '%{http_code}\n'   # 202

curl -s "$URL" "${H[@]}" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | grep -o '"name":"<connector_id>_[^"]*"'
```

Replace `<connector_id>` with your id (for example, `petstore_review`). Works in bash and zsh. A healthy host prints a session id, `202`, then one line per tool (20 for the Swagger Petstore v2 spec). An empty session id means the port is not reachable — check `NW_MCP_HOST`. A session but no tool names means `NW_MCP_SCOPE_POLICY_DEFAULT=allow` is missing (see the next paragraph). A plain `curl http://localhost:8081/mcp` returns `406 Not Acceptable`; that only proves the port answers.

The generated Dockerfile is multi-stage and digest-pinned (`python:3.13-slim@sha256:…`): wheels install in a `deps` stage (BuildKit pip cache), then `/usr/local` is copied into the runtime stage with app sources last for layer caching. It runs as non-root `USER app` with a read-only application tree, and copies only wheels (`node-wire-runtime`, `node-wire-bindings`, connector), `config/connectors.yaml`, and the thin host package (`src/<module>/`, copied to `/app/src` and put on `PYTHONPATH=/app/src`; the entrypoint is `python -m <module>`). No node-wire source tree is vendored: runtime, bindings and connector come only from the installed wheels. `.dockerignore` is an allowlist so `.env`, tenant YAML, and keys never enter the build context. MCP auth is **not** disabled in the image, and the scope policy defaults **fail-closed** (`deny`) there too — unlike local `uv run`, which sets `NW_MCP_AUTH_DISABLED=true` and `NW_MCP_SCOPE_POLICY_DEFAULT=allow` automatically for Inspector convenience. A container started without both set accepts connections but `tools/list` comes back empty. Set `NW_MCP_AUTH_DISABLED=true` / `NW_MCP_SCOPE_POLICY_DEFAULT=allow` at run time for local Inspector/ToolHive use (the generated `.env.example` already contains both).

`--env-file` injects process environment. Do not bind-mount `.env` into the container filesystem.

**MCP SDK:** Pin `mcp>=1.6.0,<2`. MCP SDK 2.x breaks `@server.list_tools()` on the current server binding.

---

## Multi-tenancy (MCP)

A generated host is a thin wrapper around the same `McpServer`, so it has the same multi-tenancy:
set `NW_MULTITENANCY_ENABLED=true` and provide `NW_TENANTS_PATH` (see the
[environment table](#environment-variables-generated-host)). Tenant resolution per transport, the
`nw_*` tenant/config tools and the ToolHive recipe are in [Tenancy](../architecture/tenancy.md).
After upgrading node-wire, regenerate and rebuild the host so its bundled bindings wheel picks up the
current tools.

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| `Missing runtime wheel in .../dist (omit --skip-build-wheels)` (or the bindings/connector equivalent) | Run without `--skip-build-wheels`, or `bash scripts/build-packages.sh packages/runtime` |
| `No pure-Python (py3-none-any) ... wheel` / image cannot install `.whl` | `dist/` only has compiled wheels from an older build: rebuild with `nw gen-whl` (or `bash scripts/build-packages.sh <package>`), then regenerate with `--skip-build-wheels` |
| `Output project already exists` | Pass `--force-output` |
| `No module named node_wire_runtime.policies` | Rebuild runtime wheel (`nw gen-whl --runtime`) — `policies` must be a package with `__init__.py` |
| `No module named bindings` / `No node-wire-bindings wheel` | Build bindings: `nw gen-whl --bindings` |
| Empty or wrong tools in fixture | `--force-fixture` to rescan `logic.py` |
| 503 / auth errors from MCP server | Ensure `NW_MCP_AUTH_DISABLED=true` (env or project `.env`) for local use |
| Connector secrets missing in Docker/ToolHive | Set secrets via `docker run -e` / `--env-file` or the orchestrator. Do not COPY or bind-mount `.env` into the image. |
| Connector action fails at runtime | Fill connector secrets via env or project `.env`; check `config/connectors.yaml` |
| `curl: (56) Recv failure: Connection reset by peer` against a running container; log shows `Uvicorn running on http://127.0.0.1:…` | Add `-e NW_MCP_HOST=0.0.0.0` to `docker run` (see [Docker](#docker)) |
| Wrong listen port in ToolHive | Set `NW_MCP_PORT` to the same value as ToolHive `FASTMCP_PORT` / `MCP_PORT` |
| `AttributeError: 'Server' object has no attribute 'list_tools'` | Pin `mcp>=1.6.0,<2` in the image Dockerfile and rebuild |
| `Config 'X' is not defined for connector 'Y'` (MT) | Use a config name that exists on every connector you call, or add it in `tenants.yaml` / REST |

---

## Package layout (source)

The tool's source tree:

```text
nw-mcp-builder/
  pyproject.toml
  README.md
  fixtures/                 # *_nw.yaml scope files
  out/                      # generated MCP hosts (gitignored content typical)
  src/nw_mcp_builder/
    cli.py                  # entrypoint: nw-mcp-builder
    connector_class.py      # loads the connector class to read its actions
    from_connector.py       # wheels → fixture → pipeline → .env.example
    tool_listing.py         # tool listing size check and list/search choice
    pipeline.py             # run_connector_pipeline
    generate/
      connector_project.py  # emit out/<server>-mcp/
    schema/
      models.py             # MCPScope validation for fixtures
```

Dependencies: `pydantic`, `pyyaml`, `node-wire-bindings` (measures the tool listing) and `node-wire-runtime` (loads connector classes). There is no mcp-builder dependency.

Unit tests live under `tests/nw_mcp_builder/` (run from the node-wire repo root):

```bash
uv run pytest tests/nw_mcp_builder -v --no-cov
```

---

## Relationship to mcp-builder

The connector-mode logic originated in stacklok's **mcp-builder** (`mcp-builder from-connector`).
**nw-mcp-builder** is a minimal copy that lives inside node-wire, so it has no dependency on that
project. It generates a host for a connector that already exists. To generate the connector from an
OpenAPI spec, use [nw-connector-builder](nw-connector-builder.md) or [`nw gen-all`](nw-cli.md). For
a stacklok-generated server with AI-curated tools, use
[`nw gen-stacklok`](../stacklok-mcp-servers.md), which vendors stacklok's generator separately.
