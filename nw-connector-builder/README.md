<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# nw-connector-builder

Generate a `node_wire_<id>` connector (and optionally an MCP server) from a
Swagger 2.0 / OpenAPI 3.x document.

**Full documentation:** [docs/cli/nw-connector-builder.md](../docs/cli/nw-connector-builder.md)

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

See also: [Build a connector](../docs/connectors-build.md), [nw-mcp-builder](../docs/cli/nw-mcp-builder.md), [packaging.md](../docs/packaging.md).
