<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Google Drive Connector

This document covers the Google Drive connector under `connectors/google_drive` in two parts:

1. **[Google Drive service account setup](#google-drive-service-account-setup)** — Create a GCP service account, enable the Drive API, configure `.env`, share a folder, and verify connectivity.
2. **[REST API reference](#rest-api-reference)** — The `execute` action, all seven operations, request/response shapes, and the platform error taxonomy.

For **MCP** (e.g. ToolHive), tools are named `google_drive.<action>` from the connector manifest (e.g. `google_drive.files.upload`). End-to-end agent setup is documented in [docs/toolhive_agent_scenario.md](toolhive_agent_scenario.md).

---

## Google Drive service account setup

This guide walks you through creating a Google Cloud service account and connecting it to Node Wire. A service account is a special type of Google account used by applications (rather than humans) to authenticate with Google APIs.

**Time to complete:** ~10 minutes

### Prerequisites

- A Google account
- Access to [Google Cloud Console](https://console.cloud.google.com/)
- Node Wire installed and configured (see [Installation Guide](installation.md))

### Step 1: Create or Select a GCP Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Click the project dropdown at the top of the page.
3. Click **New Project** (top right of the dialog).
4. Give it a name (e.g., `node-wire`) and click **Create**.
5. Make sure the new project is selected in the dropdown before continuing.

> **Already have a project?** You can use an existing one — just make sure it's selected.

### Step 2: Enable the Google Drive API

The Drive API must be enabled before your service account can access Drive.

**Via the Console UI:**

1. In the left sidebar, go to **APIs & Services > Library**.
2. Search for `Google Drive API`.
3. Click on it, then click **Enable**.

**Via the `gcloud` CLI (alternative):**

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable drive.googleapis.com
```

### Step 3: Create a Service Account

1. In the left sidebar, go to **IAM & Admin > Service Accounts**.
2. Click **+ Create Service Account** at the top.
3. Fill in the details:
   - **Service account name:** e.g., `node-wire-drive-connector`
   - **Service account ID:** auto-filled (e.g., `node-wire-drive-connector@your-project.iam.gserviceaccount.com`)
   - **Description:** e.g., `Service account for Node Wire Drive access`
4. Click **Create and Continue**.
5. On the **Grant access** step: you can skip role assignment here (Drive access is controlled by folder sharing, not IAM roles). Click **Continue**.
6. Click **Done**.

### Step 4: Download the JSON Key

The service account needs a key file so the platform can authenticate as it.

1. On the **Service Accounts** list page, find the account you just created.
2. Click the three-dot menu (Actions) on the right → **Manage keys**.
3. Click **Add Key > Create new key**.
4. Select **JSON** and click **Create**.
5. A `.json` file will be downloaded to your computer. **Keep this file safe** — it grants full access to anything the service account can access.

The file looks like this:

```json
{
  "type": "service_account",
  "project_id": "your-project-id",
  "private_key_id": "abc123...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n",
  "client_email": "node-wire-drive-connector@your-project.iam.gserviceaccount.com",
  "client_id": "123456789",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  ...
}
```

### Step 5: Configure the Connector

Move the downloaded JSON key file to a safe location on your machine (e.g., `~/.secrets/google-drive-sa.json`).

Add the following to your `.env` file:

```env
# Absolute path to your downloaded service account JSON key file
GOOGLE_DRIVE_SA_JSON=/Users/you/.secrets/google-drive-sa.json
```

**For Windows (PowerShell) — alternative to editing `.env` directly:**

```powershell
# Read the service account JSON and set it as an environment variable
$saPath = "C:\path\to\service_account.json"  # Replace with your actual path
$env:GOOGLE_DRIVE_SA_JSON = Get-Content -Path $saPath -Raw
```

> This sets the variable for the current PowerShell session. To persist it across sessions, use `[System.Environment]::SetEnvironmentVariable("GOOGLE_DRIVE_SA_JSON", (Get-Content -Path $saPath -Raw), "User")` or add the path to your `.env` file manually.

> **For ToolHive deployment:** Instead of a file path, paste the entire JSON content as a single-line string into the ToolHive secret named `GOOGLE_DRIVE_SA_JSON`. See [docs/toolhive_agent_scenario.md](toolhive_agent_scenario.md).

### Step 6: Share a Google Drive Folder with the Service Account

The service account starts with no access to any Drive files. You must explicitly share folders or files with it, just like sharing with any other Google user.

1. Open [Google Drive](https://drive.google.com/) in your browser.
2. Create a new folder (or use an existing one) where the connector will upload files.
3. Right-click the folder → **Share**.
4. In the **Add people and groups** field, paste the service account's email address. You can find it in the downloaded JSON file under `"client_email"`, or in the Cloud Console under IAM & Admin > Service Accounts.

   Example: `node-wire-drive-connector@your-project.iam.gserviceaccount.com`

5. Set the role to **Editor** (so the connector can create and upload files).
6. Uncheck **Notify people** (the service account doesn't have an inbox).
7. Click **Share**.

### Step 7: Get the Folder ID

The folder ID is used to tell the connector where to upload files.

1. Open the shared folder in Google Drive.
2. Look at the URL in your browser — it will look like:
   ```
   https://drive.google.com/drive/folders/1ABCdef_GHIjklMNOpqrSTUvwxYZ
   ```
3. The folder ID is the string after `/folders/` — in this example: `1ABCdef_GHIjklMNOpqrSTUvwxYZ`.

Add it to your `.env`:

```env
GOOGLE_DRIVE_FOLDER_ID=1ABCdef_GHIjklMNOpqrSTUvwxYZ
```

### Verification

Start the platform and test the connection with a quick file list:

```bash
# Start the REST API
python -m bindings_entrypoint

# In another terminal, list files visible to the service account
curl -X POST http://localhost:8000/connectors/google_drive/execute \
  -H "Content-Type: application/json" \
  -d '{"action": "files.list", "page_size": 5}'
```

A successful response includes `"success": true` and a `files` array under `data.raw`. See [files.list](#fileslist) for the full response shape and optional fields.

You can also use the **Swagger UI** at `http://localhost:8000/docs` to test interactively.

To upload a test file, use the request body documented under [files.upload](#filesupload) (include `parents` with your folder ID).

### Common Errors (setup)

| Error | Likely Cause | Fix |
|---|---|---|
| `GDRIVE_AUTH` / `401` or `403` | Service account key file path is wrong, or the JSON is invalid | Double-check `GOOGLE_DRIVE_SA_JSON` points to the correct absolute path |
| `GDRIVE_AUTH` / `403` on a specific file | Service account doesn't have permission to that file/folder | Share the folder with the service account email |
| `GDRIVE_BUSINESS_RULE` / `404` | Folder ID is wrong | Check the URL in Drive and re-copy the folder ID |
| `FileNotFoundError` | `GOOGLE_DRIVE_SA_JSON` path doesn't exist | Use an absolute path, not a relative one |

### Security Notes

- **Never commit the JSON key file to version control.** Add it to `.gitignore`:
  ```
  *.json
  service-account*.json
  *-sa.json
  ```
- Store the key in a secrets manager (e.g., AWS Secrets Manager, HashiCorp Vault) in production environments.
- Use a dedicated service account per application — don't reuse accounts across services.
- Grant only the minimum required permissions. If the connector only needs to upload files to one specific folder, only share that folder.
- Rotate the key periodically: Cloud Console → IAM & Admin → Service Accounts → Manage Keys → Add new key, then delete the old one.

---

## REST API reference

The connector exposes a single action `execute` with a discriminated-union payload. The `action` field decides which Google Drive operation runs. All responses share a common output shape and error taxonomy enforced by the runtime.

### Operations overview

All requests go through:

- Connector ID: `google_drive`
- REST endpoint: `POST /connectors/google_drive/execute`

Each operation uses `action` as a discriminator:

- `files.list`
- `files.create`
- `permissions.create`
- `files.get`
- `files.update`
- `files.upload`
- `files.delete`

#### files.list

List files visible to the service account.

Request body:

```json
{
  "action": "files.list",
  "page_size": 10,
  "query": "name contains 'test'"
}
```

Fields:

- `page_size` (int, optional, default 10, 1–100): maximum files to return.
- `query` (string, optional): Drive search query (`q` parameter).

Typical success response (wrapped by the runtime):

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

#### files.create

Create a new file metadata entry (no content) or create inside a folder.

Request body:

```json
{
  "action": "files.create",
  "name": "example.txt",
  "mime_type": "text/plain",
  "parents": ["<FOLDER_ID>"]
}
```

Fields:

- `name` (string, required): file name.
- `mime_type` (string, optional): Drive MIME type.
- `parents` (array of string, optional): parent folder IDs.

The service account must have write access to the parent folder.

#### permissions.create

Grant a user access to a file.

Request body:

```json
{
  "action": "permissions.create",
  "file_id": "<FILE_ID>",
  "role": "reader",
  "email_address": "user@example.com"
}
```

Fields:

- `file_id` (string, required): ID of the target file.
- `role` (string, required): `"reader"`, `"commenter"`, `"writer"`, or `"owner"`.
- `email_address` (string, required): email to grant access to.

The service account must have permission to change sharing on the file.

#### files.get

Fetch metadata for a specific file.

Request body:

```json
{
  "action": "files.get",
  "file_id": "<FILE_ID>",
  "fields": "id,name,mimeType,parents"
}
```

Fields:

- `file_id` (string, required).
- `fields` (string, optional): fields mask passed to Drive. If omitted, the connector uses a safe default (`id,name,mimeType,parents`).

#### files.update

Update file metadata and parent relationships.

Request body (rename a file and move it to a different folder):

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

Fields:

- `file_id` (string, required).
- `name` (string, optional): new file name.
- `mime_type` (string, optional): new MIME type.
- `add_parents` (array of string, optional): folder IDs to add (mapped to `addParents`).
- `remove_parents` (array of string, optional): folder IDs to remove (mapped to `removeParents`).

The service account must have edit permission on the file.

#### files.upload

Create a new file with content (text or binary).

Request body:

```json
{
  "action": "files.upload",
  "name": "hello.txt",
  "mime_type": "text/plain",
  "parents": ["<FOLDER_ID>"],
  "content": "Hello from Node Wire connector!"
}
```

For **MCP** (`google_drive.files.upload`), omit `action` in the tool arguments object; the server injects `files.upload` from the tool name. The published `inputSchema` does not include an `action` property.

Fields:

- `name` (string, required).
- `mime_type` (string, required).
- `parents` (array of string, optional).
- `content` (string, optional): UTF-8 text content that will be uploaded.
- `content_base64` (string, optional): base64-encoded binary content (e.g. PDFs, images).

Exactly one of `content` or `content_base64` must be provided (enforced at validation).

Content is uploaded using `MediaInMemoryUpload`; this is suitable for small payloads.

> For MCP callers (e.g. ToolHive): use canonical fields (`content` / `content_base64`). Legacy `media` / `media_body` shapes are normalized when possible but are not part of the public schema. Legacy `action: "upload"` in the payload is deprecated; set `NODE_WIRE_LEGACY_GDRIVE_ACTION_UPLOAD=reject` to hard-fail during rollout.

#### files.delete

Delete a file.

Request body:

```json
{
  "action": "files.delete",
  "file_id": "<FILE_ID>"
}
```

Fields:

- `file_id` (string, required).

The service account must have permission to delete the file. On success, the connector returns a small synthetic payload:

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

### Error taxonomy

All Google Drive errors are mapped into the platform error taxonomy using wrapper exceptions:

- `GDRIVE_AUTH` (`AUTH`): authentication/authorization issues (e.g. 401, 403 without rate-limit reason).
- `GDRIVE_RATE_LIMIT` (`RETRYABLE`): quota or rate limit issues (403 with `quotaExceeded`/`rateLimitExceeded`, 429, 5xx).
- `GDRIVE_BUSINESS_RULE` (`BUSINESS`): invalid IDs, conflicts, not found (400, 404, 409).
- `GDRIVE_FATAL` (`FATAL`): any unhandled `HttpError` status or unexpected exception.

The REST API maps these categories to HTTP status codes:

- `BUSINESS` → 400
- `AUTH` → 401
- `RETRYABLE` → 503
- `FATAL` → 500

The connector never raises raw `HttpError`; it always translates errors into one of these exception classes so Layer A can apply consistent retry and circuit-breaker behavior.

### Related

- AI-orchestrated workflows (ToolHive, MCP agent): [docs/toolhive_agent_scenario.md](toolhive_agent_scenario.md)
