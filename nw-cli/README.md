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

---

## Commands

From the **node-wire** repo root:

```bash
cd nw-cli && uv sync
cd ..

# One-shot: codegen → Linux wheels → MCP host → wire
uv run --project nw-cli nw generate --id pet_store --path ./openapi.yaml

# Standalone stages
uv run --project nw-cli nw wheel --id pet_store
uv run --project nw-cli nw wheel --runtime
uv run --project nw-cli nw mcp --id pet_store --force-output
uv run --project nw-cli nw docker-build --id pet_store --tag latest
```

ToolHive deploy/verify stays manual — see `scripts/deploy-openapi-mcp-toolhive.md`.

### Tests

From the node-wire repo root:

```bash
uv run --project nw-cli pytest tests/nw_cli -v -o addopts=
```
