<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-cli

Unified CLI (`nw`) for the OpenAPI → connector → wheel → MCP → Docker pipeline.

| Path | Purpose |
|------|---------|
| `src/nw_cli/` | Typer app and stage helpers |
| `../docs/nw-cli.md` | Full reference |

Installed with the monorepo `dev` group — after `uv sync` at the **node-wire** repo root, `nw` is on PATH via `uv run`.

---

## Commands

From the **node-wire** repo root:

```bash
uv sync

# One-shot: codegen → Linux wheels → MCP host → wire
uv run nw generate --id pet_store --path ./openapi.yaml

# Standalone stages
uv run nw wheel --id pet_store
uv run nw wheel --runtime
uv run nw mcp --id pet_store --force-output
uv run nw docker-build --id pet_store --tag latest
```

ToolHive deploy/verify stays manual — see `scripts/deploy-openapi-mcp-toolhive.md`.

### Tests

From the node-wire repo root:

```bash
uv run pytest tests/nw_cli -v --no-cov
```
