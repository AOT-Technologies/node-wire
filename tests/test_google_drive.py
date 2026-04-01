from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from connectors.google_drive.exceptions import (
    GoogleDriveAuthError,
    GoogleDriveBusinessError,
    GoogleDriveFatalError,
    GoogleDriveRateLimitError,
)
from connectors.google_drive.logic import DEFAULT_LIST_FIELDS, GoogleDriveConnector
from connectors.google_drive.schema import GoogleDriveOperationInput, GoogleDriveOperationOutput
from runtime import SecretProvider


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "GOOGLE_DRIVE_SA_JSON": '{"type":"service_account","project_id":"dummy"}',
        }[key]


class DummyHttpError(Exception):
    def __init__(self, status: int, *, content: str = "", reason: str = "") -> None:
        super().__init__(reason or f"http {status}")
        self.resp = SimpleNamespace(status=status)
        self.content = content
        self.reason = reason


def _connector() -> GoogleDriveConnector:
    return GoogleDriveConnector(secret_provider=MockSecretProvider())


def test_google_drive_internal_execute_files_list_happy_path():
    connector = _connector()
    params = GoogleDriveOperationInput.model_validate({"action": "files.list", "page_size": 5})

    drive = MagicMock()
    files_api = drive.files.return_value
    list_call = files_api.list.return_value
    list_call.execute.return_value = {"files": [{"id": "f-1", "name": "Report"}]}

    with patch.object(connector, "get_client", return_value=drive):
        result = asyncio.run(connector.internal_execute(params, trace_id="test-trace"))

    assert result.raw == {"files": [{"id": "f-1", "name": "Report"}]}
    assert result.description == "Successfully executed files.list"
    files_api.list.assert_called_once_with(
        pageSize=5,
        q=None,
        fields=DEFAULT_LIST_FIELDS,
        pageToken=None,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    )


@pytest.mark.parametrize(
    ("status", "content", "reason", "expected_exception"),
    [
        (403, "", "forbidden", GoogleDriveAuthError),
        (403, "rateLimitExceeded", "forbidden", GoogleDriveRateLimitError),
        (429, "", "too many requests", GoogleDriveRateLimitError),
        (404, "", "not found", GoogleDriveBusinessError),
        (418, "", "teapot", GoogleDriveFatalError),
    ],
)
def test_google_drive_http_error_translation(
    status: int, content: str, reason: str, expected_exception: type[Exception]
):
    connector = _connector()
    err = DummyHttpError(status, content=content, reason=reason)

    with pytest.raises(expected_exception):
        connector._translate_and_raise_http_error(err)  # type: ignore[arg-type]


def test_google_drive_schema_discriminator_validation():
    parsed = GoogleDriveOperationInput.model_validate({"action": "files.get", "file_id": "abc123"})
    assert parsed.root.action == "files.get"

    with pytest.raises(ValidationError):
        GoogleDriveOperationInput.model_validate({"action": "files.unknown", "file_id": "abc123"})
