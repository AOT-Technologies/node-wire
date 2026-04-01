"""Tests for Google Drive action specs and SDK call mapping."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from connectors.google_drive.action_spec import GOOGLE_DRIVE_ACTION_SPECS
from connectors.google_drive.logic import GoogleDriveConnector
from connectors.google_drive.schema import GoogleDriveOperationInput
from runtime import SecretProvider


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "GOOGLE_DRIVE_SA_JSON": '{"type":"service_account","project_id":"dummy"}',
        }[key]


def _connector() -> GoogleDriveConnector:
    return GoogleDriveConnector(secret_provider=MockSecretProvider())


def test_action_spec_registry_covers_all_sdk_actions():
    """Every @sdk_action on GoogleDriveConnector must have a spec entry."""
    metas = GoogleDriveConnector.sdk_action_metas()
    for action_name in metas:
        assert action_name in GOOGLE_DRIVE_ACTION_SPECS, f"missing spec for {action_name}"


def test_files_create_maps_body_and_constants():
    connector = _connector()
    params = GoogleDriveOperationInput.model_validate(
        {
            "action": "files.create",
            "name": "doc.txt",
            "mime_type": "text/plain",
            "parents": ["p1"],
        }
    )

    drive = MagicMock()
    files_api = drive.files.return_value
    create_call = files_api.create.return_value
    create_call.execute.return_value = {"id": "new-id", "name": "doc.txt"}

    with patch.object(connector, "get_client", return_value=drive):
        result = asyncio.run(connector.internal_execute(params, trace_id="t"))

    assert result.raw == {"id": "new-id", "name": "doc.txt"}
    files_api.create.assert_called_once_with(
        body={"name": "doc.txt", "mimeType": "text/plain", "parents": ["p1"]},
        fields="id, name, webViewLink",
        supportsAllDrives=True,
    )


def test_files_delete_returns_synthetic_raw():
    connector = _connector()
    params = GoogleDriveOperationInput.model_validate(
        {"action": "files.delete", "file_id": "fid-99"}
    )

    drive = MagicMock()
    files_api = drive.files.return_value
    upd = files_api.update.return_value
    upd.execute.return_value = {"id": "fid-99", "trashed": True}

    with patch.object(connector, "get_client", return_value=drive):
        result = asyncio.run(connector.internal_execute(params, trace_id="t"))

    assert result.raw == {"file_id": "fid-99", "status": "deleted"}
    files_api.update.assert_called_once_with(
        fileId="fid-99",
        body={"trashed": True},
        supportsAllDrives=True,
    )


def test_permissions_create_maps_body():
    connector = _connector()
    params = GoogleDriveOperationInput.model_validate(
        {
            "action": "permissions.create",
            "file_id": "f1",
            "role": "reader",
            "type": "user",
            "email_address": "a@b.com",
        }
    )

    drive = MagicMock()
    perms = drive.permissions.return_value
    perms.create.return_value.execute.return_value = {"id": "perm-1"}

    with patch.object(connector, "get_client", return_value=drive):
        result = asyncio.run(connector.internal_execute(params, trace_id="t"))

    assert result.raw == {"id": "perm-1"}
    perms.create.assert_called_once_with(
        fileId="f1",
        body={"role": "reader", "type": "user", "emailAddress": "a@b.com"},
        supportsAllDrives=True,
    )


def test_permissions_create_excludes_empty_optional_fields():
    """Empty-string email_address and domain must be excluded from the body (not sent as "")."""
    connector = _connector()
    params = GoogleDriveOperationInput.model_validate(
        {
            "action": "permissions.create",
            "file_id": "file-abc",
            "role": "reader",
            "type": "anyone",
            "email_address": "",
            "domain": "",
        }
    )

    drive = MagicMock()
    perms = drive.permissions.return_value
    perms.create.return_value.execute.return_value = {"kind": "drive#permission"}

    with patch.object(connector, "get_client", return_value=drive):
        asyncio.run(connector.internal_execute(params, trace_id="t-empty"))

    _, kwargs = perms.create.call_args
    body = kwargs["body"]
    assert "emailAddress" not in body
    assert "domain" not in body
