<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-slack

Slack connector for Node Wire. Lets an agent or app post messages to channels, send direct
messages to users, and upload files, using a Slack bot token.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-slack
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-slack`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `slack`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `post_message` | `slack_post_message` | Posts a message, with optional Block Kit blocks, to a channel ID, `#channel-name` or user |
| `send_direct_message` | `slack_send_direct_message` | Sends a private message to a user, opening the DM if needed |
| `upload_file` | `slack_upload_file` | Uploads a file (base64 content, or a file from the attachments directory) and shares it to a channel or DM |

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  slack:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: static_token
      secret_key: SLACK_BOT_TOKEN
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Bot user OAuth token (`xoxb-...`). Needs the `chat:write`, `files:write` and `im:write` scopes |
| `NW_SLACK_ATTACHMENTS_DIR` | Optional. The only directory `upload_file` may read from disk (default `/slack_attachments`) |
| `NW_SLACK_UPLOAD_LIMIT_MB` | Optional. Largest file `upload_file` accepts, in MB (default `50`, capped at `100`) |

## Usage

Set `NW_ALLOWED_CONNECTORS=slack` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Security notes

`upload_file` reads local files only from inside `NW_SLACK_ATTACHMENTS_DIR`. The path must be
absolute, and is refused if it resolves outside that directory, including through a symlink.

## Documentation

- [Slack setup guide](https://aot-technologies.github.io/node-wire/slack_connector/): bot setup, channel resolution and error codes
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#slack)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
