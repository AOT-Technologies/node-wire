<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# node-wire-google-drive

Google Drive connector for Node Wire. Lets an agent or app list, read, create, update and
delete files and folders in Google Drive (API v3) and share them with other users (49 actions; upload up to 5 MB, no download). It authenticates as a
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

The wheel is pure Python (`py3-none-any`) and installs on CPython 3.11, 3.12 and 3.13 on any
OS and CPU.

## Actions

The connector registers as `google_drive`. MCP tool names are `<connector_id>_<action>`, with
dots replaced by underscores.

| Group | Actions |
|---|---|
| About (1) | `drive_about_get` |
| Files (12) | `drive_files_list`, `drive_files_get`, `drive_files_create`, `drive_files_update`, `drive_files_copy`, `drive_files_delete`, `drive_files_empty_trash`, `drive_files_export`, `drive_files_generate_ids`, `drive_files_list_labels`, `drive_files_modify_labels`, `drive_files_watch` |
| Permissions (5) | `drive_permissions_list`, `drive_permissions_get`, `drive_permissions_create`, `drive_permissions_update`, `drive_permissions_delete` |
| Comments (5) | `drive_comments_list`, `drive_comments_get`, `drive_comments_create`, `drive_comments_update`, `drive_comments_delete` |
| Replies (5) | `drive_replies_list`, `drive_replies_get`, `drive_replies_create`, `drive_replies_update`, `drive_replies_delete` |
| Revisions (4) | `drive_revisions_list`, `drive_revisions_get`, `drive_revisions_update`, `drive_revisions_delete` |
| Shared drives (7) | `drive_drives_list`, `drive_drives_get`, `drive_drives_create`, `drive_drives_update`, `drive_drives_delete`, `drive_drives_hide`, `drive_drives_unhide` |
| Team drives, older API (5) | `drive_teamdrives_list`, `drive_teamdrives_get`, `drive_teamdrives_create`, `drive_teamdrives_update`, `drive_teamdrives_delete` |
| Changes and channels (4) | `drive_changes_get_start_page_token`, `drive_changes_list`, `drive_changes_watch`, `drive_channels_stop` |

MCP tool names are `google_drive_<action>`, for example `google_drive_drive_files_list`.

**Content, compared with the earlier hand-written connector:** `drive_files_upload` (new, hand-written) uploads a file with its content in one multipart request, up to 5 MB, with `content` (text) or `content_base64` (binary). `drive_files_create` and `drive_files_update` take `name`, `mime_type`, `description` and `parents` as JSON (so they make folders, rename and move files), and `trashed: true` on update moves a file to the trash (what the old delete did; the generated `drive_files_delete` is permanent, and a service account that is only an Editor of a shared drive may be refused). Shared drives are on by default (`supportsAllDrives`, and `includeItemsFromAllDrives` on list), as before. **Still not supported:** downloading file content, replacing the content of an existing file, and uploads over 5 MB (resumable). `drive_files_export` does not return binary formats correctly.

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

- [Node Wire on GitHub](https://github.com/AOT-Technologies/node-wire): source, issues and full documentation
- [Google Drive setup guide](https://aot-technologies.github.io/node-wire/google_drive_connector/): service-account setup, per-user access and error codes
- [Connector reference](https://aot-technologies.github.io/node-wire/connector-reference/#google_drive)
- [Configuration](https://aot-technologies.github.io/node-wire/configuration/)
- [Error codes](https://aot-technologies.github.io/node-wire/errors/)
- [Changelog](https://github.com/AOT-Technologies/node-wire/blob/main/CHANGELOG.md)

## License

Apache-2.0. See [LICENSE](https://github.com/AOT-Technologies/node-wire/blob/main/LICENSE).
