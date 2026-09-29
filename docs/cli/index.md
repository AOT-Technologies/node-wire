<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# CLI

The command-line toolchain that turns an OpenAPI/Swagger spec into a Node Wire connector,
wheels, an MCP host, and a Docker image (`nw gen-all` for the first three, `nw docker-build` for the image).

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
| [Generator contract](nw-connector-builder-scope.md) | What the generator supports, and what it deliberately does not. This is the contract; the how-to is nw-connector-builder |
| [Codegen behaviour](nw-connector-builder-codegen.md) | Why generated code looks the way it does — read this when output surprises you |
| [nw-mcp-builder](nw-mcp-builder.md) | Turning a connector into a standalone MCP server project, run directly |

## Which tool do I use?

| I want to… | Use | Page |
|---|---|---|
| Turn an OpenAPI/Swagger spec into a connector, wheels and MCP host | **`nw gen-all`** | [nw CLI](nw-cli.md#nw-gen-all) |
| Build the Docker image for that MCP host | `nw docker-build` | [nw CLI](nw-cli.md#nw-docker-build) |
| Build an MCP server whose tools are curated by AI scoping | **`nw gen-stacklok`** | [stacklok MCP servers](../stacklok-mcp-servers.md) |
| Regenerate just the MCP host for an existing connector | `nw gen-mcp` | [nw CLI](nw-cli.md#nw-gen-mcp) |
| Rebuild just the wheels | `nw gen-whl` | [nw CLI](nw-cli.md#nw-gen-whl) |
| Call an existing connector, per tenant | *no CLI*: `ConnectorFactory` or the REST API | [Use a connector](../connectors.md) |
| Write a connector by hand (SDK-style or non-REST) | *no CLI*: author `schema.py` + `logic.py` | [Build a connector](../connectors-build.md) |
| Deploy an MCP server to ToolHive and drive it with an agent | *no CLI*: `thv` + the bundled agent | [ToolHive scenario](../toolhive_agent_scenario.md) |

**Advanced / direct access.** `nw-connector-builder` and `nw-mcp-builder` remain supported as
standalone entry points and expose the full flag surface. Read them when you need a flag `nw` does
not pass through, or are debugging a single stage.

## Related

| Doc | When to read it |
|-----|-----------------|
| [MCP overview](../mcp.md) | Transports, tool search, multi-tenancy |
| [stacklok MCP servers](../stacklok-mcp-servers.md) | `nw gen-stacklok` in depth |
| [Packaging](../packaging.md) | `build-packages.sh`, wheels, PyPI |
| [Configuration](../configuration.md) | `connectors.yaml` and `NW_*` variables |
