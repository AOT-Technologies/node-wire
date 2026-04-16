from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from node_wire_google_drive.exceptions import (
    GoogleDriveAuthError,
    GoogleDriveBusinessError,
    GoogleDriveFatalError,
    GoogleDriveRateLimitError,
)
from node_wire_google_drive.logic import DEFAULT_LIST_FIELDS, GoogleDriveConnector
from node_wire_google_drive.schema import (
    FilesUploadOperation,
    GoogleDriveOperationInput,
    GoogleDriveOperationOutput,
)
from node_wire_runtime import SecretProvider


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "GOOGLE_DRIVE_SA_JSON": '{"type":"service_account","project_id":"dummy"}',
        }[key]


class StaticSecretProvider(SecretProvider):
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, key: str) -> str:
        return self._secrets[key]


class DummyHttpError(Exception):
    def __init__(self, status: int, *, content: str = "", reason: str = "") -> None:
        super().__init__(reason or f"http {status}")
        self.resp = SimpleNamespace(status=status)
        self.content = content
        self.reason = reason


def _connector() -> GoogleDriveConnector:
    return GoogleDriveConnector(secret_provider=MockSecretProvider())


def test_files_upload_operation_requires_exactly_one_body_source() -> None:
    FilesUploadOperation.model_validate(
        {
            "action": "files.upload",
            "name": "a.txt",
            "mime_type": "text/plain",
            "content": "hello",
        }
    )
    with pytest.raises(ValidationError):
        FilesUploadOperation.model_validate(
            {
                "action": "files.upload",
                "name": "a.txt",
                "mime_type": "text/plain",
            }
        )
    with pytest.raises(ValidationError):
        FilesUploadOperation.model_validate(
            {
                "action": "files.upload",
                "name": "a.txt",
                "mime_type": "text/plain",
                "content": "a",
                "content_base64": "Zg==",
            }
        )


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


def test_google_drive_build_client_accepts_json_secret() -> None:
    provider = StaticSecretProvider(
        {"GOOGLE_DRIVE_SA_JSON": '{"type":"service_account","project_id":"dummy"}'}
    )
    connector = GoogleDriveConnector(secret_provider=provider)
    creds_obj = object()
    client_obj = object()

    with (
        patch(
            "node_wire_google_drive.logic.service_account.Credentials.from_service_account_info",
            return_value=creds_obj,
        ) as mocked_from_info,
        patch("node_wire_google_drive.logic.build", return_value=client_obj) as mocked_build,
    ):
        client = connector.build_client()

    mocked_from_info.assert_called_once()
    mocked_build.assert_called_once_with("drive", "v3", credentials=creds_obj)
    assert client is client_obj


def test_google_drive_build_client_rejects_non_json_secret() -> None:
    provider = StaticSecretProvider({"GOOGLE_DRIVE_SA_JSON": "C:\\secrets\\gdrive-sa.json"})
    connector = GoogleDriveConnector(secret_provider=provider)

    with pytest.raises(GoogleDriveAuthError, match="must contain valid service account JSON"):
        connector.build_client()


def test_files_list_query_blank_normalizes_to_none() -> None:
    parsed = GoogleDriveOperationInput.model_validate(
        {"action": "files.list", "query": "   ", "page_size": 10}
    )
    assert parsed.root.query is None


def test_files_list_query_rejects_control_chars() -> None:
    with pytest.raises(ValidationError):
        GoogleDriveOperationInput.model_validate(
            {"action": "files.list", "query": "name contains 'abc'\n", "page_size": 10}
        )


def test_files_list_query_rejects_overlong_value() -> None:
    with pytest.raises(ValidationError):
        GoogleDriveOperationInput.model_validate(
            {"action": "files.list", "query": "a" * 1025, "page_size": 10}
        )


def test_files_list_query_allows_normal_value() -> None:
    parsed = GoogleDriveOperationInput.model_validate(
        {"action": "files.list", "query": "name contains 'report'", "page_size": 10}
    )
    assert parsed.root.query == "name contains 'report'"
