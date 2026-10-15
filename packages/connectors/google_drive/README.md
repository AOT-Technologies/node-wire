<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-google-drive

Google Drive connector for Node Wire. Lets an agent or app list, read, create, upload, update and
delete files in Google Drive (API v3) and share them with other users. It authenticates as a
service account, or with each caller's own Google token.

Part of [Node Wire](https://github.com/AOT-Technologies/node-wire). Every call runs through the
Node Wire runtime, so it gets input validation, retries with a circuit breaker, scope policy,
a shared error taxonomy, and OpenTelemetry tracing. A Node Wire host exposes each action over REST,
gRPC and MCP.

## Install

```bash
pip install node-wire-google-drive
```

This pulls in [`node-wire-runtime`](https://pypi.org/project/node-wire-runtime/). Pre-releases
are published as `X.Y.ZbN`; install one with `pip install --pre node-wire-google-drive`. To install
every connector at once, use [`node-wire-connectors`](https://pypi.org/project/node-wire-connectors/).

Wheels are compiled with Cython and contain no Python source. They are built for CPython 3.11,
3.12 and 3.13 on Linux x86_64 (manylinux and musllinux), macOS arm64 and Windows x64.

## Actions

The connector registers as `google_drive`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Action | MCP tool | What it does |
|---|---|---|
| `files.list` | `google_drive_files_list` | Lists or searches files, with an optional Drive query and fields mask |
| `files.get` | `google_drive_files_get` | Fetches the metadata of one file |
| `files.create` | `google_drive_files_create` | Creates a file or folder entry (metadata only), optionally inside a folder |
| `files.upload` | `google_drive_files_upload` | Creates a new file with text content |
| `files.update` | `google_drive_files_update` | Updates a file's metadata or moves it between folders |
| `files.delete` | `google_drive_files_delete` | Deletes a file |
| `permissions.create` | `google_drive_permissions_create` | Shares a file with a user as reader, commenter, writer or owner |

## Configuration

Add the connector to `config/connectors.yaml`:

```yaml
connectors:
  google_drive:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
    auth:
      provider: service_account
      sa_json_secret: GOOGLE_DRIVE_SA_JSON
      scopes:
      - https://www.googleapis.com/auth/drive
```

Secrets are read through the configured secret provider (environment variables by default):

| Variable | Purpose |
|---|---|
| `GOOGLE_DRIVE_SA_JSON` | Service-account key: the JSON contents, or (locally) an absolute path to the key file |

### Per-user access (`upstream_bearer`)

Instead of a service account, the connector can forward each caller's own Google access token,
so every call runs with that user's Drive permissions. This is a trust-boundary decision: Node Wire
forwards the token as-is and does not check that it was issued for Drive. It is enabled only when
**both** of these are set, and fails closed otherwise:

1. `auth: { provider: upstream_bearer }` in `config/connectors.yaml` (or
   `GOOGLE_DRIVE_AUTH_PROVIDER=upstream_bearer`)
2. `NW_UPSTREAM_BEARER_CONNECTORS=google_drive`

## Usage

Set `NW_ALLOWED_CONNECTORS=google_drive` (comma-separate several). Node Wire loads only the
connectors listed there, and discovers them through the `node_wire.connectors` entry point.
A Node Wire host (the REST, gRPC or MCP server, or `ConnectorFactory`) then builds the connector
from the configuration above. See [Use a connector](https://aot-technologies.github.io/node-wire/connectors/).

## Documentation

- [Google Drive setup guide](https://aot-technologies.github.io/node-wire/google_drive_connector/): service-account setup, per-user access and error codes
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#google_drive)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
