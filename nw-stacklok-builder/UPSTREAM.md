<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Vendored upstream code

This folder vendors two Apache-2.0 projects from Stacklok, Inc. They are copied, not fetched at
build time. `LICENSE` is the upstream Apache-2.0 license text; see `REUSE.toml` at the repo root
for the file-level copyright annotation.

| Upstream | Commit | Vendored to |
|---|---|---|
| [stacklok/mcp-builder](https://github.com/stacklok/mcp-builder) | `7c03c38` (2026-06-01) | `src/mcp_builder/` |
| [stacklok/mcp-template-py](https://github.com/stacklok/mcp-template-py) | `2bd79dc` (2026-05-29) | `template/` |

## Files taken

**mcp-builder** — everything reachable from `pipeline.run_pipeline` and `validate.validate_scope`
(29 files):

- `__init__.py`, `log.py`, `pipeline.py`, `validate.py`
- `schema/` — `__init__.py`, `models.py`
- `spec/` — `__init__.py`, `loader.py`, `media.py`, `parameters.py`, `resolver.py`, `types.py`
- `generate/` — `__init__.py`, `plan.py`, `scaffold.py`, `patches.py`,
  `renderers/{__init__,client,escape,manifests,tools}.py`, `renderers/templates/*.jinja2` (8)

Not taken: `cli.py` and `analyze.py` (node-wire drives the pipeline from `nw gen-stacklok`;
`analyze` only feeds stacklok's AI scoping), `skills/`, `agents/`, `tests/`, `e2e/`, `docs/`, and
repository tooling (`Taskfile.yml`, `.github/`, `CLAUDE.md`, `renovate.json`, `uv.lock`, ...).

**mcp-template-py** — what the scaffold copies into a generated project: `src/mcp_template_py/`
(8 files), `pyproject.toml`, `Dockerfile`, `.dockerignore`, `.python-version`, `.env.example`,
`Taskfile.yml`, `README.md`. Not taken: `api/models.py` and `api/tools.py` (the pipeline deletes
the first and overwrites the second in every generated project), `tests/`, `docs/`, `.github/`,
`.claude/`, `CLAUDE.md`,
`CONTRIBUTING.md`, `SECURITY.md`, `renovate.json`, `.trivyignore`, `VERSION`,
`docker-compose.yml`, `uv.lock`.

## node-wire changes

node-wire logic lives in `src/nw_stacklok/`; edits to the vendored files are limited to calls into
it. The first commit of this folder is the pristine copy, so `git diff <that commit> --
nw-stacklok-builder/src/mcp_builder nw-stacklok-builder/template` shows every node-wire change.

Five vendored files are edited (each imports from `nw_stacklok` under a `# node-wire:` comment); edits only
adds optional fields or calls into `nw_stacklok`. The template (`template/`) is unmodified — the
node-wire project changes are applied to the *generated* copy by `nw_stacklok.project`.

| File | Change |
|---|---|
| `schema/models.py` | `MCPScope.runtime: NodeWireRuntime \| None` (`nw_stacklok.scope`) |
| `generate/plan.py` | Optional `ParamPlan.nw_field`, `ToolPlan.nw_action`, `ServerPlan.node_wire` |
| `generate/renderers/tools.py` | Passes `nw_action` / `nw_arguments_expr` (`nw_stacklok.render.arguments_expr`) to the template |
| `generate/renderers/templates/tools.py.jinja2` | A `{% if tool.nw_action %}` branch: the tool calls `self._client.run(action, {...})`; `Any` import |
| `pipeline.py` | `run_pipeline(..., node_wire: NodeWireOptions \| None = None)`; calls `nw_stacklok.hooks` to bind the plan (`attach`), render `client.py`, finish the project, and render manifests |

A scope without `runtime:` takes none of these branches (tested by
`tests/nw_stacklok_builder/test_vendored_pristine.py`).

`nw_stacklok` (node-wire code, not vendored):

| Module | Role |
|---|---|
| `scope.py` | `runtime:` block model and the plan's `NodeWirePlan` |
| `connector.py` | Reads the connector's `report.json` (endpoint → action) and action input models |
| `resolve.py` | Binds tools to actions and parameters to input fields; reports every mismatch |
| `render.py` | Tool argument dicts and the generated `client.py` (`APIClient` → `node_wire_toolhive.NodeWireClient`) |
| `project.py` | Wheels + `[tool.uv.sources]`, connector config, Dockerfile env, config tools, README |
| `manifests.py` | Backend Deployment/Service, NetworkPolicy, per-tenant `MCPRemoteProxy`, tenants Secret |
| `hooks.py` | `NodeWireOptions` and the functions `pipeline.py` calls |

## Updating to a newer upstream commit

1. Re-copy the same file lists from the new upstream commits and commit them alone as the new
   pristine base (update the table above).
2. Re-apply the node-wire changes listed above (`git diff <old pristine> <old head>` for these
   two directories, applied on top).
3. Run `uv run pytest tests/nw_stacklok_builder` — the pristine-parity test checks that the
   generator still behaves exactly like upstream when a scope has no `runtime:` block.
