#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Google Drive connector: schema validation and HTTP error mapping.

Simplified: Drive is an OpenAPI-generated RestConnector, so failures are httpx errors
mapped by ``error_map`` rather than googleapiclient exceptions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

import node_wire_runtime.rest as rest
from node_wire_google_drive.logic import GoogleDriveConnector
from node_wire_google_drive.schema import (
    DriveFilesExportInput,
    DriveFilesGetInput,
    DriveFilesListInput,
)
from node_wire_runtime import SecretProvider
from node_wire_runtime.models import ErrorCategory


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"GOOGLE_DRIVE_ACCESS_TOKEN": "ya29.dummy"}[key]


class _ErrorResp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.content = b"{}"
        self.text = "{}"
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        request = httpx.Request("GET", "https://www.googleapis.com/drive/v3/files")
        raise httpx.HTTPStatusError(
            "error", request=request, response=httpx.Response(self.status_code, request=request)
        )


class _Client:
    def __init__(self, response: _ErrorResp | None = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error

    async def request(self, **_kwargs: Any) -> _ErrorResp:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _ClientContext:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def __aenter__(self) -> Any:
        return self._client

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _install(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    """Route the runtime's HTTP calls to ``client`` (pooled-client and per-call runtimes)."""
    monkeypatch.setattr(rest, "assert_safe_destination", AsyncMock(return_value=None))
    if hasattr(rest, "_shared_client"):
        monkeypatch.setattr(rest, "_shared_client", lambda _trust_env: client)
    else:
        monkeypatch.setattr(rest.httpx, "AsyncClient", lambda **_kw: _ClientContext(client))


def _connector() -> GoogleDriveConnector:
    return GoogleDriveConnector(secret_provider=MockSecretProvider())


def _fake(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    _install(monkeypatch, client)


def test_google_drive_schema_rejects_unknown_and_missing_fields():
    DriveFilesListInput(pageSize=10)
    DriveFilesGetInput(fileId="fid")

    with pytest.raises(ValidationError):
        DriveFilesListInput(not_a_drive_field="x")
    with pytest.raises(ValidationError):
        DriveFilesGetInput()  # fileId is a required path parameter
    with pytest.raises(ValidationError):
        DriveFilesExportInput(fileId="fid")  # mimeType is required
    with pytest.raises(ValidationError):
        DriveFilesListInput(pageSize="many")


def test_google_drive_action_discriminator_is_fixed():
    assert DriveFilesListInput().action == "drive_files_list"
    with pytest.raises(ValidationError):
        DriveFilesListInput(action="drive_files_get")


def test_google_drive_error_map():
    error_map = _connector().error_map

    assert error_map[httpx.HTTPStatusError] == (ErrorCategory.BUSINESS, "HTTP_STATUS_ERROR")
    assert error_map[httpx.ConnectError] == (ErrorCategory.RETRYABLE, "HTTP_CONNECT_ERROR")
    assert error_map[httpx.ReadTimeout] == (ErrorCategory.RETRYABLE, "HTTP_READ_TIMEOUT")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_google_drive_http_error_translation(monkeypatch, status):
    _fake(monkeypatch, _Client(_ErrorResp(status)))

    result = await _connector().run({"action": "drive_files_get", "fileId": "missing"})

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "HTTP_STATUS_ERROR"


@pytest.mark.asyncio
async def test_google_drive_connect_error_is_retryable(monkeypatch):
    _fake(monkeypatch, _Client(error=httpx.ConnectError("down")))

    result = await _connector().run({"action": "drive_files_list"})

    assert result.success is False
    assert result.error_category == ErrorCategory.RETRYABLE
    assert result.error_code == "HTTP_CONNECT_ERROR"


@pytest.mark.asyncio
async def test_google_drive_unknown_action_is_rejected(monkeypatch):
    _fake(monkeypatch, _Client(error=httpx.ConnectError("never reached")))

    result = await _connector().run({"action": "drive_files_teleport"})

    assert result.success is False
