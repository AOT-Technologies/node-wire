<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Architecture decision records

An ADR records **why** a decision was made. It is frozen once accepted. Current behaviour lives on
the living pages. Start at the [reading map](../reading.md) for those.

| ADR | Decision | Status | Current behaviour |
|---|---|---|---|
| [0001](0001-stacklok-mcp-builder-on-node-wire.md) | stacklok MCP builder on node-wire | Accepted | [stacklok MCP servers](../stacklok-mcp-servers.md) |
| [0002](0002-documentation-lifecycle.md) | Documentation lifecycle | Accepted | [Changing the docs](../reading.md#changing-the-docs) |

## Rules

- Each record starts with `Status:` (`Accepted` or `Superseded`) and `Date:`.
- To change a decision, write a new ADR with `Supersedes: ADR NNNN`. Then add
  `Superseded by: ADR MMMM` to the old one. That back-pointer is the only edit an accepted ADR gets.
- Number ADRs in order (`NNNN-short-title.md`) and add a row to the table above.
