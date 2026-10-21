<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-toolhive

Runs [Node Wire](https://github.com/AOT-Technologies/node-wire) connectors inside MCP servers
built with [stacklok's mcp-builder](https://github.com/stacklok/mcp-builder) and deployed behind
[ToolHive](https://github.com/stacklok/toolhive). The server keeps stacklok's curated tools, and
each tool call runs through a Node Wire connector, so it gets input validation, retries with a
circuit breaker, a shared error taxonomy, and OpenTelemetry tracing.

You do not normally write code against this package. `nw gen-stacklok`, in the Node Wire
repository, generates a server that already uses it, and the server's image installs it.

## What it provides

| Feature | How it works |
|---|---|
| Credential relay | ToolHive forwards the upstream credential as `Authorization: Bearer`. `RelayAuthProvider` presents it the way the connector's auth block expects (bearer, a named header, or a query parameter), re-read on every call and never cached |
| Tenants | The tenant comes from the `X-Tenant-ID` header that each ToolHive tenant proxy sets. With `NW_PROXY_SECRET` set, the header is accepted only alongside a matching `X-NW-Proxy-Secret` |
| Config tools | `nw_list_configs` and `nw_select_config` choose a named tenant config for the MCP session |
| Errors | A failed call reaches the MCP client as `CODE [CATEGORY]: message (trace_id=...)`, with the same codes as every other Node Wire surface |
| Telemetry | `init_telemetry()` adds Node Wire's log redaction and OTLP export without replacing the server's own log format |

## Install

This package is not published to PyPI. `nw gen-stacklok` builds its wheel, copies it into the
generated project's `wheels/` directory and pins it in `[tool.uv.sources]`, so the server's image
installs that local copy. For development, install it from a Node Wire checkout:

```bash
uv pip install -e packages/runtime -e packages/bindings -e packages/toolhive
```

It depends on `node-wire-runtime`, `node-wire-bindings` and the
[MCP Python SDK](https://pypi.org/project/mcp/).

## Usage

A generated server's `client.py` wraps one connector:

```python
from node_wire_toolhive import NodeWireClient

client = NodeWireClient("http_generic", config_path="config/connectors.yaml")

# Inside a tool handler, while an MCP request is being served:
data = await client.run("request", {"method": "GET", "url": "https://httpbin.org/get"})
```

`run()` returns the action's data, or raises `NodeWireToolError` with the error code, category
and trace ID. `register_config_tools(mcp, client)` and `report_call_errors(mcp, client)` add the
config tools and error reporting to the server's `FastMCP` instance.

| Variable | Purpose |
|---|---|
| `NW_UPSTREAM_BEARER_CONNECTORS` | Connector IDs whose callers' credentials may be relayed. A connector not listed here never receives the forwarded credential |
| `NW_MULTITENANCY_ENABLED` | `true` to resolve the tenant from `X-Tenant-ID` and enable the config tools |
| `NW_PROXY_SECRET` | Shared secret the tenant proxies send as `X-NW-Proxy-Secret` |
| `NW_CONFIG_PATH` | Path to `connectors.yaml` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP/HTTP endpoint for traces, metrics and logs. Unset, nothing is exported |

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [stacklok MCP servers on Node Wire](https://aot-technologies.github.io/node-wire/stacklok-mcp-servers/): generating, deploying and verifying a server
- [MCP in Node Wire](https://aot-technologies.github.io/node-wire/mcp/): how this path compares with the other MCP hosts
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
