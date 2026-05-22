<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Google Drive Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Google Drive connector panel, and assert on the rendered
pipeline state. No mocking — every test hits the real Google Drive API.

## What is tested

| Test | Action |
|------|--------|
| `test_gdrive_list_files_default_page_size` | `files.list` — default page size |
| `test_gdrive_list_files_explicit_page_size` | `files.list` — explicit page_size=5 |
| `test_gdrive_list_files_with_query` | `files.list` — mimeType filter |
| `test_gdrive_get_file` | `files.get` — valid file ID with field mask |
| `test_gdrive_get_file_without_fields` | `files.get` — no fields mask |
| `test_gdrive_get_file_invalid_id` | `files.get` — nonexistent ID, expects error state |
| `test_gdrive_update_file_name` | `files.update` — rename file |
| `test_gdrive_update_file_name_and_mime` | `files.update` — rename + mime_type |
| `test_gdrive_upload_file` | `files.upload` — attach file, fill recipient, assert 4-step pipeline |
| `test_gdrive_upload_remove_and_reattach` | `files.upload` — remove attachment UI, re-attach |
| `test_gdrive_switch_list_then_get` | Cross-action switch on same page |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/gdrive-archival")`
calls route to the real backend, which calls the real Google Drive API.
No `page.route()` interception.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all GDrive tests
uv run pytest tests/playground/gdrive/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/gdrive/ --no-cov -v -s
```

> **Note:** GDrive tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `GOOGLE_DRIVE_SA_JSON` | Service-account JSON (path to file or full JSON string inline) |
| `GOOGLE_DRIVE_FOLDER_ID` | Google Drive folder ID where test files are uploaded |
| `GDRIVE_TEST_RECIPIENT_EMAIL` | Sharing recipient email for upload tests (default: `test@mailinator.com`) |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## CI / GitHub Actions

GDrive tests run **only on manual `workflow_dispatch`** trigger
(`Actions → CI – Pytest → Run workflow`).

Credentials are read from repository secrets:

| Secret | Maps to env var |
|--------|----------------|
| `GOOGLE_DRIVE_SA_JSON` | `GOOGLE_DRIVE_SA_JSON` |
| `GOOGLE_DRIVE_FOLDER_ID` | `GOOGLE_DRIVE_FOLDER_ID` |
| `GDRIVE_TEST_RECIPIENT_EMAIL` | `GDRIVE_TEST_RECIPIENT_EMAIL` |

Set these under **Settings → Secrets and variables → Actions** before triggering
the workflow.

## Test data and cleanup

The `uploaded_test_file_id` session fixture uploads a small file
(`nw-integration-test.txt`) to Google Drive once per test session. This file
is **not automatically deleted** after the tests finish — clean it up manually
via the Google Drive UI if needed.

The `files.update` tests rename this file but do not delete it.
