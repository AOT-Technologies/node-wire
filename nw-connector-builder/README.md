<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder

Generate a `node_wire_<id>` connector (and optionally an MCP server) from a
Swagger 2.0 / OpenAPI 3.x document.

**Full documentation:** [docs/nw-connector-builder.md](../docs/nw-connector-builder.md)

```bash
cd nw-connector-builder
uv sync

# Connector only
uv run nw-connector-builder from-openapi --path path/to/openapi.yaml --id my_api --no-mcp

# Remote spec + overwrite + wire config + MCP host
uv run nw-connector-builder from-openapi \
  --path https://petstore.swagger.io/v2/swagger.json \
  --id pet_store \
  --force \
  --wire

# MCP host from an existing connector
uv run nw-connector-builder mcp -c pet_store --force-output
```

From the node-wire repo root:

```bash
uv run --directory nw-connector-builder nw-connector-builder --help
uv run pytest tests/nw_connector_builder -v --no-cov
```

See also: [connectors.md](../docs/connectors.md), [mcp-servers.md](../docs/mcp-servers.md), [packaging.md](../docs/packaging.md).
