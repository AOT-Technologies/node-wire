<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Google Drive connector — technical documentation

> **Platform:** Node Wire
> **Connector ID:** `google_drive`
> **REST:** One route per operation, for example, `POST /connectors/google_drive/files.list` (the `action` field is still set on the body for `BaseConnector` dispatch).
> **Discriminator:** `action` field (discriminated-union payload)
> **Source:** `src/node_wire_google_drive/`

---

## Operations overview

The runtime validates requests against the discriminated union in `schema.py`, then dispatches to `@nw_action` handlers on `GoogleDriveConnector`. Each handler delegates to an **action spec** in `action_spec.py` that maps the validated model to the Google Drive API v3 client (`googleapiclient`). Shared concerns (thread offload, `HttpError` translation, logging) stay in `logic.py`. All responses share a common output shape and error taxonomy enforced by the runtime.

### Action-spec layout

The action-spec pattern has these pieces:

| Piece | Role |
|-------|------|
| [`action_spec.py`](action_spec.py) | `GOOGLE_DRIVE_ACTION_SPECS`: per-action `SdkActionSpec` (resource path, method, field/body mapping, constants, optional `build_kwargs` / `post_process`). |
| [`logic.py`](logic.py) | Client build, `_translate_and_raise_http_error`, `_execute_action_spec`, thin `@nw_action` methods. |
| [`node_wire_runtime/sdk_action_spec.py`](../node_wire_runtime/sdk_action_spec.py) | Reusable primitives: `SdkActionSpec`, `default_build_kwargs`, `execute_spec_in_thread`. |

**Adding a new operation:** Add a Pydantic variant in `schema.py` (with an `action` discriminator literal), extend the `GoogleDriveOperationInput` union, and add an entry to `GOOGLE_DRIVE_ACTION_SPECS` in `action_spec.py` (or a `build_kwargs` hook for non-generic cases such as multipart upload). `BaseConnector.__init_subclass__` auto-generates the handler — do **not** also add an `@nw_action` method for the same action name, as that will raise a `TypeError` at class-definition time.

### Migrating other connectors

Use the same pattern: put declarative mapping in a connector-local `*_action_spec` module; `BaseConnector.__init_subclass__` auto-generates `@nw_action`-equivalent handlers from `action_specs`, so no manual `@nw_action` decorators are needed for spec-driven actions. Use `SdkActionSpec.build_kwargs` when the vendor API needs custom assembly (uploads, explicit `None` args, etc.).

### Available operations

The connector supports the following actions:

| Action | Description |
|---|---|
| `files.list` | List or search files with optional query and fields mask |
| `files.create` | Create file metadata (no content) or create inside a folder |
| `permissions.create` | Grant a user access to a file (reader, commenter, writer, owner) |
| `files.get` | Fetch metadata for a specific file |
| `files.update` | Update file metadata and parent relationships |
| `files.upload` | Create a new file with text content |
| `files.delete` | Delete a file |

### Planned operations

The following operations are planned for future implementation:

- **`files.generateIds`** — Pre-generate IDs to avoid duplicate uploads during retries.
- **`changes.watch`** — Trigger workflows the moment a file is uploaded.
- **`revisions.list`** — Allows users to see who changed what and when.
- **`files.export`** — Export Google Docs, Sheets, or Slides to `.docx`, `.pdf`, or `.csv` (no traditional download; export required).

---

## Operation reference

### `files.list`

List or search files visible to the service account. The connector always sends a `fields` mask to the Drive API; if omitted, a performant default is used so only commonly needed metadata is returned.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.list"` |
| `page_size` | int | No | Default `10`, range `1–100`. Maximum files to return |
| `query` | string | No | Drive search query (`q` parameter) |
| `fields` | string | No | Fields mask. Default: `"nextPageToken, files(id, name, mimeType, webViewLink)"` |

**Request body — minimal (default fields):**

```json
{
  "action": "files.list",
  "page_size": 10
}
```

**Request body — with query and custom fields:**

```json
{
  "action": "files.list",
  "page_size": 20,
  "query": "mimeType = 'application/pdf'",
  "fields": "nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)"
}
```

**Typical success response:**

```json
{
  "success": true,
  "data": {
    "raw": {
      "files": [
        { "id": "1...", "name": "example.txt", "mimeType": "text/plain" }
      ]
    },
    "description": "Successfully executed files.list"
  },
  "error_code": null,
  "error_category": null,
  "message": null,
  "trace_id": "..."
}
```

---

### `files.create`

Create a new file metadata entry (no content) or create inside a folder. The service account must have write access to the parent folder.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.create"` |
| `name` | string | ✅ | File name |
| `mime_type` | string | No | Drive MIME type |
| `parents` | array of string | No | Parent folder IDs |

```json
{
  "action": "files.create",
  "name": "example.txt",
  "mime_type": "text/plain",
  "parents": ["<FOLDER_ID>"]
}
```

---

### `permissions.create`

Grant a user access to a file. The service account must have permission to change sharing on the file.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"permissions.create"` |
| `file_id` | string | ✅ | ID of the target file |
| `role` | string | ✅ | `"reader"`, `"commenter"`, `"writer"`, or `"owner"` |
| `email_address` | string | ✅ | Email address to grant access to |

```json
{
  "action": "permissions.create",
  "file_id": "<FILE_ID>",
  "role": "reader",
  "email_address": "user@example.com"
}
```

---

### `files.get`

Fetch metadata for a specific file.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.get"` |
| `file_id` | string | ✅ | ID of the target file |
| `fields` | string | No | Fields mask passed to Drive. Default: `"id,name,mimeType,parents"` |

```json
{
  "action": "files.get",
  "file_id": "<FILE_ID>",
  "fields": "id,name,mimeType,parents"
}
```

---

### `files.update`

Update file metadata and parent relationships. The service account must have edit permission on the file.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.update"` |
| `file_id` | string | ✅ | ID of the file to update |
| `name` | string | No | New file name |
| `mime_type` | string | No | New MIME type |
| `add_parents` | array of string | No | Folder IDs to add (mapped to `addParents`) |
| `remove_parents` | array of string | No | Folder IDs to remove (mapped to `removeParents`) |

```json
{
  "action": "files.update",
  "file_id": "<FILE_ID>",
  "name": "renamed.txt",
  "mime_type": "text/plain",
  "add_parents": ["<NEW_FOLDER_ID>"],
  "remove_parents": ["<OLD_FOLDER_ID>"]
}
```

---

### `files.upload`

Create a new file with text content. Content is uploaded using `MediaInMemoryUpload`; suitable for small text payloads.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.upload"` |
| `name` | string | ✅ | File name |
| `mime_type` | string | ✅ | MIME type |
| `parents` | array of string | No | Parent folder IDs |
| `content` | string | ✅ | UTF-8 text content to upload |

```json
{
  "action": "files.upload",
  "name": "hello.txt",
  "mime_type": "text/plain",
  "parents": ["<FOLDER_ID>"],
  "content": "Hello from Node Wire connector!"
}
```

---

### `files.delete`

Delete a file. The service account must have permission to delete the file.

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | ✅ | Must be `"files.delete"` |
| `file_id` | string | ✅ | ID of the file to delete |

```json
{
  "action": "files.delete",
  "file_id": "<FILE_ID>"
}
```

On success, the connector returns a synthetic confirmation payload:

```json
{
  "success": true,
  "data": {
    "raw": {
      "file_id": "<FILE_ID>",
      "status": "deleted"
    },
    "description": "Successfully executed files.delete"
  },
  "error_code": null,
  "error_category": null,
  "message": null,
  "trace_id": "..."
}
```

---

## Error taxonomy

All Google Drive errors are mapped into the platform error taxonomy using wrapper exceptions. The connector never raises raw `HttpError` — it always translates errors into one of these exception classes so the platform can apply consistent retry and circuit-breaker behaviour.

| Error Code | Category | Trigger Conditions |
|---|---|---|
| `GDRIVE_AUTH` | `AUTH` | Authentication/authorisation issues (401, 403 without rate-limit reason) |
| `GDRIVE_RATE_LIMIT` | `RETRYABLE` | Quota or rate limit issues (403 `quotaExceeded`/`rateLimitExceeded`, 429, 5xx) |
| `GDRIVE_BUSINESS_RULE` | `BUSINESS` | Invalid IDs, conflicts, not found (400, 404, 409) |
| `GDRIVE_FATAL` | `FATAL` | Any unhandled `HttpError` status or unexpected exception |

### HTTP status code mapping

Each error category maps to an HTTP status:

| Category | HTTP Status |
|---|---|
| `BUSINESS` | `400` |
| `AUTH` | `401` |
| `RETRYABLE` | `503` |
| `FATAL` | `500` |
