<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-stacklok-builder

[stacklok mcp-builder](https://github.com/stacklok/mcp-builder) (vendored, `src/mcp_builder/`) and
its [mcp-template-py](https://github.com/stacklok/mcp-template-py) template (`template/`), modified
so the MCP servers they generate run on the node-wire runtime and a node-wire connector.

- Driven by `nw gen-stacklok`; see [docs/stacklok-mcp-servers.md](../docs/stacklok-mcp-servers.md).
- Phase 1 (AI scoping) is stacklok's own `skills/ai-scoping` and `agents/`. Link them into
  `.claude/` with `scripts/install-stacklok-skills.sh`. Their CLI calls (`uv run mcp-builder analyze`
  / `validate`, `uv run mcp-builder-schema`) resolve to this package.
- Upstream commits, the exact files taken, and every node-wire change: [UPSTREAM.md](UPSTREAM.md).
- node-wire code lives in `src/nw_stacklok/`; vendored files only gain hook calls.
- Output goes to `out/` (gitignored).
- Python 3.13 (stacklok's requirement).

Tests: `uv run pytest tests/nw_stacklok_builder` (pristine parity, node-wire mode, and an
in-process run of a generated server).
