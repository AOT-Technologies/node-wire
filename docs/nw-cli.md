<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw CLI

`nw` is the unified CLI for the **new** OpenAPI → connector → wheel → MCP host → Docker image pipeline. It orchestrates [`nw-connector-builder`](nw-connector-builder.md), [`scripts/build-packages.sh`](packaging.md), and [`nw-mcp-builder`](mcp-servers.md) without replacing those tools.

ToolHive deploy/verify (`thv`) is **out of scope** — `nw` stops at `docker-build`. See `scripts/deploy-openapi-mcp-toolhive.md` for manual deploy steps.

---

## Install

From the **node-wire** repo root (hard assumption — there is no `--node-wire-root` flag):

```bash
cd nw-cli && uv sync && cd ..
uv run --project nw-cli nw --help
```

Prefer `uv run --project nw-cli` over `--directory nw-cli` so the working directory stays the repo root.

---

## Commands

| Command | What it does |
|---------|----------------|
| `nw generate` | One-shot: connector codegen → Linux wheels → MCP host → wire |
| `nw wheel` | Standalone wheel build via `scripts/build-packages.sh` |
| `nw mcp` | Standalone MCP host (requires existing wheels) |
| `nw docker-build` | `docker build` inside `nw-mcp-builder/out/<server>-mcp/` |

```mermaid
flowchart LR
  generate["nw generate"] --> runBuild["run_build no_mcp=True"]
  generate --> wheelStage["build-packages.sh"]
  generate --> mcpStage["run_from_connector"]
  generate --> allPkgs["ALL_PACKAGES"]
  wheelCmd["nw wheel"] --> wheelStage
  mcpCmd["nw mcp"] --> mcpStage
  dockerCmd["nw docker-build"] --> dockerBuild["docker build"]
```

### `nw generate`

```bash
uv run --project nw-cli nw generate \
  --id pet_store \
  --path path/to/openapi.yaml
```

| Flag | Effect |
|------|--------|
| `--id` | Connector id (required) |
| `--path` | OpenAPI/Swagger file or URL (required) |
| `--no-wheel` | Skip wheel build |
| `--no-mcp` | Skip MCP host build |
| `--no-wire` | Skip `connectors.yaml` / `sample.env` / `ALL_PACKAGES` registration |
| `--force` | Overwrite existing connector / MCP output |

Stages are **in-process** function calls (never re-invokes `nw`). Connector codegen always passes `no_mcp=True` to `run_build` so the builder’s host-only MCP hand-off is skipped; MCP uses `skip_build_wheels=True` against wheels from `build-packages.sh`.

When wire is enabled, `run_build` updates `config/connectors.yaml` and `sample.env`, and `nw` also inserts `packages/connectors/<id>` into `scripts/build-packages.sh`’s `ALL_PACKAGES` list if missing.

### `nw wheel`

```bash
uv run --project nw-cli nw wheel --id pet_store          # Linux-only (default)
uv run --project nw-cli nw wheel --id pet_store --host   # host-only
uv run --project nw-cli nw wheel --id pet_store --all    # cibuildwheel matrix
uv run --project nw-cli nw wheel --runtime               # packages/runtime only
```

Default mode is **`--linux-only`** (not the script’s host+Linux combined default). Runtime is not rebuilt with every connector build — use `--runtime` when needed.

### `nw mcp`

```bash
uv run --project nw-cli nw mcp --id pet_store
uv run --project nw-cli nw mcp --id pet_store --force-output
```

Always `skip_build_wheels=True`. If wheels are missing:

- **Interactive (TTY):** prompts to build the missing prerequisite
- **Non-interactive:** exits non-zero with the exact fix command (e.g. `nw wheel --runtime`)

There is no `--yes` / auto-confirm flag.

### `nw docker-build`

```bash
uv run --project nw-cli nw docker-build --id pet_store
uv run --project nw-cli nw docker-build --id pet_store --tag v1
```

Builds `docker build -t <id-with-hyphens>-nw-mcp:<tag> .` inside `nw-mcp-builder/out/<server>-mcp/`. `--tag` defaults to `latest`.

---

## Output

`nw generate` uses brand-colored `rich.progress` (amber spinner, blue bar, pink on failure) with a bordered summary panel. Single-stage commands use a simpler status spinner.

---

## Relationship to sibling CLIs

| Tool | Role |
|------|------|
| `nw` | Orchestrator for the happy path |
| `nw-connector-builder` | Still available for low-level OpenAPI codegen |
| `nw-mcp-builder` | Still available for MCP-only generation |

Deprecating the standalone builder entry points is **not** part of this CLI.

---

## Tests

```bash
uv run --project nw-cli pytest tests/nw_cli -v -o addopts=
```

Coverage is unit/mocked only (no live Docker or network spec fetch).
