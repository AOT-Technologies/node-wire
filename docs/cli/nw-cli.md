<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw CLI

`nw` is the unified CLI for turning an OpenAPI spec into a connector, wheels, an MCP host, and a Docker image. `gen-all` runs connector → wheel → MCP host → wire; the image is a separate `docker-build` step. It orchestrates [`nw-connector-builder`](nw-connector-builder.md), [`scripts/build-packages.sh`](../packaging.md), and [`nw-mcp-builder`](nw-mcp-builder.md) without replacing those tools.

ToolHive deploy/verify (`thv`) is **out of scope** — `nw` stops at `docker-build`. For manual ToolHive registration and the end-to-end agent path, see [nw-mcp-builder](nw-mcp-builder.md#platform-and-toolhive-read-this-first) and [toolhive_agent_scenario.md](../toolhive_agent_scenario.md).

---

## Install

`nw-cli` is part of the monorepo **dev** dependency group. From the **node-wire** repo root (hard assumption — there is no `--node-wire-root` flag):

```bash
uv sync
uv run nw --help
uv run nw --version   # or -V
```

---

## Commands

| Command | What it does |
|---------|----------------|
| `nw gen-all` | One-shot: connector codegen → Linux wheels → MCP host → wire |
| `nw gen-whl` | Standalone wheel build via `scripts/build-packages.sh` |
| `nw gen-mcp` | Standalone MCP host (requires existing wheels) |
| `nw docker-build` | `docker build` inside `nw-mcp-builder/out/<server>-mcp/` |
| `nw gen-stacklok` | Scope an OpenAPI spec with stacklok's `/ai-scoping`, then build a stacklok MCP server on the node-wire runtime |

### `nw gen-all`

```bash
uv run nw gen-all \
  --connector-id pet_store \
  --path path/to/openapi.yaml
```

| Flag | Effect |
|------|--------|
| `--connector-id` | Connector id (required) |
| `--path` | OpenAPI/Swagger file or URL (required) |
| `--no-wheel` | Skip wheel build |
| `--no-mcp` | Skip MCP host build |
| `--no-wire` | Skip `connectors.yaml` / `sample.env` / `ALL_PACKAGES` registration |
| `--force` | Overwrite existing connector / MCP output |
| `--tool-search` | MCP host serves tools through `nw_search_tools` + `nw_call_tool` (no prompt) |
| `--full-tool-list` | MCP host lists every tool, even over budget (no prompt, no warning) |
| `--max-tool-listing-kb` | Tool listing budget in KB (default `25`) |

Stages run in-process and in order, each skippable independently; the `mcp` stage additionally checks for wheels before building and can trigger a build-or-prompt sub-step of its own:

```mermaid
flowchart TD
    Start(["nw gen-all"]) --> Connector["Connector codegen<br/>run_build(no_mcp=True)"]
    Connector --> McpWanted{"Build an<br/>MCP host?"}

    McpWanted -- "yes" --> ToolMode["Decide tool mode<br/>measure listing against --max-tool-listing-kb<br/>TTY: ask · non-TTY: full list + warning"]
    McpWanted -- "no (--no-mcp)" --> WheelGate
    ToolMode --> WheelGate{"Build<br/>wheels?"}

    WheelGate -- "yes" --> Wheel["Wheel build<br/>runtime + bindings, then connector"]
    WheelGate -- "no (--no-wheel)" --> McpGate
    Wheel --> McpGate{"MCP host<br/>requested?"}

    McpGate -- "yes" --> WheelsPresent{"Wheels present<br/>for this id?"}
    McpGate -- "no (--no-mcp)" --> WireGate
    WheelsPresent -- "yes" --> McpBuild["MCP host build<br/>run_mcp_build"]
    WheelsPresent -- "no, TTY" --> Prompt["Prompt to build<br/>the missing wheel"]
    WheelsPresent -- "no, non-TTY" --> Fail(["exit 1<br/>prints the fix command"])
    Prompt --> McpBuild
    McpBuild --> WireGate{"Wire the<br/>connector in?"}

    WireGate -- "yes" --> Wire["Wire<br/>connectors.yaml + sample.env + ALL_PACKAGES"]
    WireGate -- "no (--no-wire)" --> Done(["Done"])
    Wire --> Done

    classDef stage fill:#ddf1fb,stroke:#1a88b0,stroke-width:1px,color:#0d2f3d
    classDef gate fill:#fdf2d6,stroke:#b8860b,stroke-width:1px,color:#3a2c05
    classDef term fill:#eceff3,stroke:#5b7387,stroke-width:1px,color:#1c2733
    classDef bad fill:#fde2ea,stroke:#b81548,stroke-width:1px,color:#3d0a1d
    class Connector,ToolMode,Wheel,McpBuild,Prompt,Wire stage
    class McpWanted,WheelGate,McpGate,WheelsPresent,WireGate gate
    class Start,Done term
    class Fail bad
```

Stages are **in-process** function calls (never re-invokes `nw`). Connector codegen always passes `no_mcp=True` to `run_build` so the builder’s host-only MCP hand-off is skipped; MCP uses `skip_build_wheels=True` against wheels from `build-packages.sh`.

When wire is enabled:

- `run_build(..., wire=True)` updates `config/connectors.yaml` and `sample.env`
- `nw` inserts `packages/connectors/<id>` into `scripts/build-packages.sh`’s `ALL_PACKAGES` list if missing

If MCP runs and wheels are missing, the same TTY / non-TTY prerequisite handling as `gen-mcp` applies (see below).

**Tool mode.** Right after codegen (before the wheel builds), the connector's MCP tool listing is measured. Over the budget (`--max-tool-listing-kb`, default 25 KB) the progress bars pause and you are asked how the MCP host should expose its tools — the full tool list, or tool search (`nw_search_tools` + `nw_call_tool`, schemas on demand) — with an explanation of where tool search can fail. Without a terminal the full list is generated and a warning names the flags. `--tool-search` / `--full-tool-list` decide up front. The build never fails on size; the choice becomes the host's default `NW_MCP_TOOL_MODE`. Wording and logic live in nw-mcp-builder (`nw_mcp_builder/tool_listing.py`); see its README.

### `nw gen-whl`

```bash
uv run nw gen-whl --connector-id pet_store          # Linux-only (default)
uv run nw gen-whl --connector-id pet_store --host   # host-only
uv run nw gen-whl --connector-id pet_store --all    # cibuildwheel matrix
uv run nw gen-whl --runtime                         # packages/runtime only
```

Default mode passes **`--linux-only`** to `scripts/build-packages.sh` (not the script’s host+Linux combined default). The CLI does not expose a `--linux-only` flag — omit `--host` / `--all` to get that mode. `--host` and `--all` are mutually exclusive. Runtime is not rebuilt with every connector build — use `--runtime` when needed. `--connector-id` is required unless `--runtime` and/or `--bindings` is set. `--bindings` builds `packages/bindings` (the MCP host surface) — `gen-mcp` asks you to run `nw gen-whl --bindings` when that wheel is missing.

### `nw gen-mcp`

```bash
uv run nw gen-mcp --connector-id pet_store
uv run nw gen-mcp --connector-id pet_store --force-output
uv run nw gen-mcp --connector-id pet_store --tool-search
```

Takes the same `--tool-search` / `--full-tool-list` / `--max-tool-listing-kb` flags as `gen-all`, and asks the same question before building the host.

Always `skip_build_wheels=True`. If the runtime or connector wheel is missing:

- **Interactive (TTY):** prompts to build the missing prerequisite
- **Non-interactive:** exits non-zero with the exact fix command (e.g. `nw gen-whl --runtime`)

There is no `--yes` / auto-confirm flag.

### `nw docker-build`

```bash
uv run nw docker-build --connector-id pet_store
uv run nw docker-build --connector-id pet_store --tag v1
```

Builds `docker build -t <hyphenated-id>-nw-mcp:<tag> .` inside `nw-mcp-builder/out/<hyphenated-id>-nw-mcp/` (e.g. `pet_store` → image `pet-store-nw-mcp:latest`, project dir `…/out/pet-store-nw-mcp/`). `--tag` defaults to `latest`. Pass secrets at **run** time (`docker run --env-file` / `-e`); they are not baked into the image.

If the MCP project directory is missing, the same TTY / non-TTY prompt offers to run `nw gen-mcp` first.

### `nw gen-stacklok`

```bash
uv run nw gen-stacklok --path <spec path or URL> --connector-id pet_store \
  [--workflow "..." ...] [--auth-hint "..."] [--scoping-notes "..."]
uv run nw gen-stacklok --scope mcp-scope.yaml --connector-id pet_store      # reviewed scope → Phase 3
uv run nw gen-stacklok --scope mcp-scope.yaml --force --no-wheel --no-lock --output-dir out/
```

With `--path` it runs stacklok's Phases 1–3 in one go:
1. prepares the spec;
2. opens Claude Code with the `/ai-scoping` skill, which asks its questions and stops at its
   approval gates as in stacklok's flow. `--workflow`, `--auth-hint` and `--scoping-notes` are
   starting answers. Exit the session to continue. `--headless`, or no terminal, runs it
   unattended with `claude -p`;
3. **pauses for the human review**: `y` continues, `n` stops and prints the `--scope` command to
   resume with;
4. generates.

A second `--path` run reuses the scope in `nw-stacklok-builder/scoping/<id>/`; `--rescope` redoes
it. The connector is always rebuilt from the spec. An existing output project is checked before
anything is built: you're asked whether to replace it, and `--force` replaces it without asking.

Builds a [stacklok mcp-builder](https://github.com/stacklok/mcp-builder) server on the node-wire
runtime from a stacklok `mcp-scope.yaml`: connector codegen from the scope's `spec.source` →
wheels for the image (cp313 musllinux for stacklok's Alpine base, via `scripts/build-packages.sh
--cibw-linux` in Docker; unchanged packages are reused) → the vendored stacklok
generator (`nw-stacklok-builder/out/<server>-mcp/`, then `uv lock`). `--connector-id` may be
omitted when the scope has a `runtime: {type: node_wire, connector_id: ...}` block. See
[stacklok MCP servers](../stacklok-mcp-servers.md).

---

## Output

`nw gen-all` uses brand-colored `rich.progress` (amber spinner, blue bar, pink on failure) with a bordered summary panel. Single-stage commands use a simpler status spinner.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Stage failure, missing prerequisite (non-interactive / declined), or root resolution error |
| `2` | Usage error (e.g. `--host` with `--all`, or missing `--connector-id` without `--runtime` or `--bindings`) |

---

## Relationship to sibling CLIs

| Tool | Role |
|------|------|
| `nw` | Orchestrator for the happy path |
| `nw-connector-builder` | Still available for low-level OpenAPI codegen |
| `nw-mcp-builder` | Still available for MCP-only generation |
| `nw-stacklok-builder` | Vendored stacklok generator driven by `nw gen-stacklok`. Ships stacklok's `mcp-builder` (analyze / validate / generate) and `mcp-builder-schema`, which the ai-scoping skill calls; you rarely run them yourself |

Deprecating the standalone builder entry points is **not** part of this CLI.

---

## Tests

```bash
uv run pytest tests/nw_cli -v --no-cov
```

Coverage is unit/mocked only (no live Docker or network spec fetch).

---

## Related docs

| Doc | When to read it |
|-----|-----------------|
| [nw-connector-builder.md](nw-connector-builder.md) | OpenAPI → connector codegen details |
| [nw-mcp-builder](nw-mcp-builder.md) | Generated MCP host layout, ToolHive, Inspector |
| [packaging.md](../packaging.md) | `build-packages.sh`, wheels, PyPI |
| [configuration.md](../configuration.md) | `connectors.yaml` and env vars |
