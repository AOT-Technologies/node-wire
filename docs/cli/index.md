<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# CLI

The command-line toolchain that turns an OpenAPI/Swagger spec into a Node Wire connector,
wheels, an MCP host, and a Docker image.

`nw` is the orchestrator. It calls the other tools for you, so it is the only one most
people need.

```bash
uv run nw gen-all --connector-id pet_store --path path/to/openapi.yaml
```

## Pages

| Page | What it covers |
|---|---|
| [nw CLI](nw-cli.md) | The orchestrator: `gen-all`, `gen-whl`, `gen-mcp`, `docker-build`, `gen-stacklok` |
| [nw-connector-builder](nw-connector-builder.md) | The OpenAPI → connector generator, run directly |
| [Scope](nw-connector-builder-scope.md) | What the generator supports, and what it deliberately does not |
| [Codegen behaviour](nw-connector-builder-codegen.md) | Why generated code looks the way it does — read this when output surprises you |
| [nw-mcp-builder](nw-mcp-builder.md) | Turning a connector into a standalone MCP server project, run directly |

## Which command?

| I want to… | Command |
|---|---|
| Spec → connector → wheels → MCP host → image, in one shot | `nw gen-all` |
| Rebuild only the wheels | `nw gen-whl` |
| Rebuild only the MCP host | `nw gen-mcp` |
| Build the Docker image | `nw docker-build` |
| Build a stacklok MCP server with AI-curated tools | `nw gen-stacklok` |

Writing a connector **by hand** instead? No CLI is involved — see the
[Connectors guide](../connectors.md).

## Related

| Doc | When to read it |
|-----|-----------------|
| [MCP overview](../mcp.md) | Transports, tool search, multi-tenancy |
| [stacklok MCP servers](../stacklok-mcp-servers.md) | `nw gen-stacklok` in depth |
| [Packaging](../packaging.md) | `build-packages.sh`, wheels, PyPI |
| [Configuration](../configuration.md) | `connectors.yaml` and `NW_*` variables |
