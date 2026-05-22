#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import base64
import os

import httpx
import pytest

_TEST_RECIPIENT_EMAIL = os.environ.get(
    "GDRIVE_TEST_RECIPIENT_EMAIL", "test@mailinator.com"
)


@pytest.fixture(scope="session")
def real_gdrive_file_id(api_server_url: str) -> str:
    """Return a real Google Drive file ID by listing the Drive via the API.

    Skips the test if no files exist in the configured Drive folder.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/gdrive-archival",
            json={"action": "files.list", "list_page_size": 5},
        )
    resp.raise_for_status()
    data = resp.json()
    files = data.get("steps", [{}])[0].get("data", {}).get("raw", {}).get("files", [])
    if not files:
        pytest.skip("No files found in Google Drive — skipping tests that need a real file ID")
    return files[0]["id"]


@pytest.fixture(scope="session")
def uploaded_test_file_id(api_server_url: str) -> str:
    """Upload a small test file to Google Drive once per session and return its ID.

    Used by files.update tests so they operate on a disposable file.
    Note: the file is left in Google Drive after the session (manual cleanup needed).
    """
    content = b"node-wire integration test file - safe to delete"
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/gdrive-archival",
            json={
                "action": "files.upload",
                "document_name": "nw-integration-test.txt",
                "recipient_email": _TEST_RECIPIENT_EMAIL,
                "file_base64": base64.b64encode(content).decode(),
                "file_mime_type": "text/plain",
            },
        )
    resp.raise_for_status()
    data = resp.json()
    file_id = data.get("final_resource_id")
    if not file_id:
        pytest.skip(
            f"Setup upload failed — cannot run update tests. "
            f"Error: {data.get('error_message') or 'no file_id returned'}"
        )
    return file_id


@pytest.fixture(scope="session")
def test_recipient_email() -> str:
    """Email address used as the sharing recipient in upload tests."""
    return _TEST_RECIPIENT_EMAIL
