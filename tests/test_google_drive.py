#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
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
    FilesListOperation,
    FilesUploadOperation,
    GoogleDriveOperationInput,
)
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth import ServiceAccountAuthProvider
from node_wire_runtime.auth.base import reset_upstream_bearer, set_upstream_bearer


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
    sp = MockSecretProvider()
    return GoogleDriveConnector(
        secret_provider=sp,
        auth_provider=ServiceAccountAuthProvider(
            secret_provider=sp,
            sa_json_secret="GOOGLE_DRIVE_SA_JSON",
        ),
    )


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


@pytest.mark.asyncio
async def test_google_drive_upstream_bearer_uses_request_token() -> None:
    from bindings.factory import ConnectorFactory

    sp = MockSecretProvider()
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp
    provider = factory._build_auth_provider(
        "google_drive", {"auth": {"provider": "upstream_bearer"}}
    )
    connector = GoogleDriveConnector(secret_provider=sp, auth_provider=provider)

    drive = MagicMock()
    files_api = drive.files.return_value
    list_call = files_api.list.return_value
    list_call.execute.return_value = {"files": []}

    ctx = set_upstream_bearer("google-token-a")
    try:
        with patch("node_wire_google_drive.logic.build", return_value=drive) as mock_build:
            await connector._execute_action_spec(
                "files.list",
                FilesListOperation(action="files.list", page_size=5),
                trace_id="trace-1",
            )
            creds = mock_build.call_args.kwargs["credentials"]
            assert creds.token == "google-token-a"
    finally:
        reset_upstream_bearer(ctx)


@pytest.mark.asyncio
async def test_google_drive_upstream_bearer_no_token_raises() -> None:
    from bindings.factory import ConnectorFactory

    sp = MockSecretProvider()
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp
    provider = factory._build_auth_provider(
        "google_drive", {"auth": {"provider": "upstream_bearer"}}
    )
    connector = GoogleDriveConnector(secret_provider=sp, auth_provider=provider)

    with pytest.raises(GoogleDriveAuthError, match="Upstream bearer token required"):
        await connector._execute_action_spec(
            "files.list",
            FilesListOperation(action="files.list", page_size=5),
            trace_id="trace-1",
        )


@pytest.mark.parametrize(
    ("action", "payload", "status", "expected_exception"),
    [
        (
            "files.upload",
            {
                "name": "upload.txt",
                "mime_type": "text/plain",
                "content": "hello",
            },
            403,
            GoogleDriveAuthError,
        ),
        (
            "permissions.create",
            {
                "file_id": "f1",
                "role": "reader",
                "type": "user",
                "email_address": "a@b.com",
            },
            404,
            GoogleDriveBusinessError,
        ),
    ],
)
def test_google_drive_execute_translates_http_errors(
    action: str,
    payload: dict,
    status: int,
    expected_exception: type[Exception],
) -> None:
    from googleapiclient.errors import HttpError

    connector = _connector()
    params = GoogleDriveOperationInput.model_validate({"action": action, **payload})

    async def _raise_http_error(*_args: object, **_kwargs: object) -> None:
        resp = MagicMock()
        resp.status = status
        resp.reason = "upstream error"
        raise HttpError(resp, b"error")

    with (
        patch.object(connector, "get_client", return_value=MagicMock()),
        patch(
            "node_wire_google_drive.logic.execute_spec_in_thread",
            side_effect=_raise_http_error,
        ),
    ):
        with pytest.raises(expected_exception):
            asyncio.run(connector.internal_execute(params, trace_id="test-trace"))


@pytest.mark.asyncio
async def test_google_drive_unknown_action_spec_raises():
    connector = _connector()
    with pytest.raises(ValueError, match="No action spec registered"):
        await connector._execute_action_spec("not_a_real_action", {}, trace_id="test-trace")
