<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-http

Generic HTTP connector for Node Wire. Lets an agent or app call any REST endpoint (`GET`, `POST`,
`PUT`, `PATCH` or `DELETE`) without a dedicated connector, while blocking requests to internal
network addresses.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-http
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-http`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `http_generic`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `request` | `http_generic_request` | Sends one HTTP request (method, URL, query parameters, headers, body) and returns the status code, headers and body |

## Configuration

Add the connector to `config/connectors.yaml`. It needs no credentials: the whole request is the
action payload.

```yaml
connectors:
  http_generic:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
```

| Variable | Purpose |
|---|---|
| `NW_HTTP_GENERIC_ALLOWED_HOSTS` | Optional. Comma- or space-separated hostnames; when set, requests to any other host are refused |

## Usage

Set `NW_ALLOWED_CONNECTORS=http_generic` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

Because this connector needs no credentials, you can also run it directly with only the PyPI
packages installed:

```python
import asyncio
import os

os.environ["NW_ALLOWED_CONNECTORS"] = "http_generic"
os.environ["NW_HTTP_GENERIC_ALLOWED_HOSTS"] = "httpbin.org"

from node_wire_runtime import get_connector_registry
from node_wire_runtime.connector_registry import auto_register


async def main() -> None:
    auto_register()
    connector = get_connector_registry()["http_generic"]()
    response = await connector.run(
        {"action": "request", "method": "GET", "url": "https://httpbin.org/get"}
    )
    print(response.success, response.data["status_code"])


asyncio.run(main())
```

## Security notes

- Requests to loopback, private and link-local addresses and to cloud metadata endpoints are
  refused. The check runs when the payload is validated, and again against the resolved IP
  address at connection time.
- `NW_HTTP_GENERIC_ALLOWED_HOSTS` narrows egress to an explicit allowlist. Set it in production.
- Logs omit query strings and URL fragments.

## Documentation

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#http_generic)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
