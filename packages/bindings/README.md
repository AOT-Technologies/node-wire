<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-bindings

MCP host surface for Node Wire: `ConnectorFactory`, shared `invoke`, and
`bindings.mcp_server.McpServer`. Generated MCP Docker images install this wheel
instead of vendoring `src/bindings` onto `PYTHONPATH`.

REST and gRPC bindings remain in the monorepo editable install only — they are
not part of this package.
