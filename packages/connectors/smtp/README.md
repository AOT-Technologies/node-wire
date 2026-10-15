<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-smtp

SMTP connector for Node Wire. Lets an agent or app send email through a relay that is pinned on
the server, so a caller can choose the recipients and content but never the mail server.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-smtp
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-smtp`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `smtp`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `send_email` | `smtp_send_email` | Sends a plain-text email to one or more recipients (`to`, `subject`, `body`, optional `from_email`) |

When `from_email` is omitted, the sender is `FROM_EMAIL`, then `SMTP_USERNAME`.

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  smtp:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: static_credentials
      username_secret: SMTP_USERNAME
      password_secret: SMTP_PASSWORD
```

The relay is configured only through the environment. Credentials are read through the
configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | Relay hostname (default `smtp.gmail.com`) |
| `SMTP_PORT` | Relay port (default `587`) |
| `SMTP_USE_TLS` | Upgrade the connection with STARTTLS (default `true`). Port `465` always uses implicit TLS |
| `SMTP_USERNAME` | Relay login |
| `SMTP_PASSWORD` | Relay password or app password |
| `FROM_EMAIL` | Default sender address |
| `NW_SMTP_ALLOWED_HOSTS` | Optional. Comma-separated relay hostnames; the connector refuses to send if `SMTP_HOST` is not listed |

## Usage

Set `NW_ALLOWED_CONNECTORS=smtp` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Security notes

- The payload cannot set the relay host, port or TLS mode; those fields are dropped. Relay
  credentials therefore never go to a host chosen by the caller.
- Set `NW_SMTP_ALLOWED_HOSTS` in production.
- `subject` may not contain newlines or control characters, which blocks header injection.

## Documentation

- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#smtp)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
