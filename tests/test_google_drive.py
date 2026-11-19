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
    DriveFilesCreateInput,
    DriveFilesExportInput,
    DriveFilesGetInput,
    DriveFilesListInput,
    DriveFilesUpdateInput,
    DriveFilesUploadInput,
)
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth import StaticTokenAuthProvider
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


# ---- content: the earlier connector's files.upload, and metadata on create and update --------------


class _Resp:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"{}") -> None:
        self.status_code = status
        self._body = {"id": "file123", "name": "a.txt"} if body is None else body
        self.content = content
        self.text = content.decode()
        self.headers = {"content-type": "application/json"}

    def json(self) -> Any:
        if not isinstance(self._body, (dict, list)):
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://www.googleapis.com/x")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )


class _Recorder:
    """Stands in for the pooled httpx client and keeps the requests it was given."""

    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"{}") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status, self.body, self.content = status, body, content

    async def request(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return _Resp(self.status, self.body, self.content)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def _authed() -> GoogleDriveConnector:
    secrets = MockSecretProvider()
    return GoogleDriveConnector(
        secret_provider=secrets,
        auth_provider=StaticTokenAuthProvider(
            secret_provider=secrets, secret_key="GOOGLE_DRIVE_ACCESS_TOKEN"
        ),
    )


def _parts(call: dict[str, Any]) -> tuple[dict[str, Any], str, bytes]:
    """The metadata, the content type and the content of a multipart/related request."""
    import json as _json
    import re

    match = re.search(r"boundary=(\S+)", call["headers"]["Content-Type"])
    assert match
    boundary = match.group(1).encode()
    first, second = call["content"].split(b"--" + boundary)[1:3]
    meta = _json.loads(first.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n"))
    head, data = second.split(b"\r\n\r\n", 1)
    ctype = head.strip().split(b"Content-Type: ")[1].decode()
    return meta, ctype, data[:-2]  # the CRLF before the next boundary belongs to the framing


async def test_upload_sends_one_multipart_request_to_the_upload_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder()
    _install(monkeypatch, rec)
    result = await _authed().run(
        {
            "action": "drive_files_upload",
            "name": "note.txt",
            "mime_type": "text/plain",
            "parents": ["folder123"],
            "description": "a note",
            "content": "hello",
        }
    )
    assert result.success, result.message
    call = rec.last
    assert call["method"] == "POST"
    base, _, query = call["url"].partition("?")
    assert base == "https://www.googleapis.com/upload/drive/v3/files"
    assert dict(p.split("=", 1) for p in query.split("&")) == {
        "uploadType": "multipart",
        "supportsAllDrives": "true",
        "fields": "id%2Cname%2CmimeType%2Csize%2Cparents%2CwebViewLink",
    }
    assert call["headers"]["Content-Type"].startswith("multipart/related; boundary=")
    assert call["headers"]["Authorization"] == "Bearer ya29.dummy"
    assert _parts(call) == (
        {
            "name": "note.txt",
            "mimeType": "text/plain",
            "parents": ["folder123"],
            "description": "a note",
        },
        "text/plain",
        b"hello",
    )
    assert result.data["status_code"] == 200 and result.data["body"]["id"] == "file123"


async def test_upload_binary_content_survives_the_framing(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    raw = bytes(range(256)) + b"\r\n--not-a-boundary\r\n"
    rec = _Recorder()
    _install(monkeypatch, rec)
    await _authed().run(
        {
            "action": "drive_files_upload",
            "name": "x.bin",
            "mime_type": "application/octet-stream",
            "content_base64": base64.b64encode(raw).decode(),
        }
    )
    meta, ctype, data = _parts(rec.last)
    assert meta == {"name": "x.bin", "mimeType": "application/octet-stream"}
    assert (ctype, data) == ("application/octet-stream", raw)


async def test_upload_can_turn_shared_drives_off_and_choose_the_answer_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder()
    _install(monkeypatch, rec)
    await _authed().run(
        {
            "action": "drive_files_upload",
            "name": "a",
            "mime_type": "text/plain",
            "content": "x",
            "supportsAllDrives": False,
            "fields": "id",
        }
    )
    assert rec.last["url"].endswith("uploadType=multipart&supportsAllDrives=false&fields=id")


async def test_upload_errors_map_like_the_other_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Recorder(status=404))
    result = await _authed().run(
        {"action": "drive_files_upload", "name": "a", "mime_type": "text/plain", "content": "x"}
    )
    assert (result.error_code, result.error_category) == ("HTTP_STATUS_ERROR", "BUSINESS")


async def test_upload_returns_a_non_json_or_empty_answer_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload = {
        "action": "drive_files_upload",
        "name": "a",
        "mime_type": "text/plain",
        "content": "x",
    }
    _install(monkeypatch, _Recorder(body="plain", content=b"plain"))
    assert (await _authed().run(upload)).data["body"] == "plain"
    _install(monkeypatch, _Recorder(content=b""))
    assert (await _authed().run(upload)).data["body"] is None


async def test_upload_needs_the_standard_drive_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Recorder())
    conn = _authed()
    monkeypatch.setattr(conn, "resolve_base_url", lambda: "https://proxy.example.com/gateway")
    with pytest.raises(ValueError, match="/drive/v3"):
        await conn.drive_files_upload(
            DriveFilesUploadInput(name="a", mimeType="text/plain", content="x"), trace_id="t"
        )


async def test_upload_logs_sizes_but_not_names_content_or_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    _install(monkeypatch, _Recorder())
    await _authed().run(
        {
            "action": "drive_files_upload",
            "name": "patient-secret-name.txt",
            "mime_type": "text/plain",
            "content": "very private content",
        }
    )
    text = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for hidden in ("patient-secret-name", "very private content", "ya29.dummy"):
        assert hidden not in text
    assert any(r.__dict__.get("bytes") == 20 for r in caplog.records)


@pytest.mark.parametrize(
    "bad",
    [
        {},  # neither content nor content_base64
        {"content": "x", "content_base64": "eA=="},  # both
        {"content_base64": "not base64!!"},
        {"content": "x" * (5 * 1024 * 1024 + 1)},
        {"content": "\u00e9" * (3 * 1024 * 1024)},  # few characters, but over 5 MB as UTF-8
        {"content": "x", "name": ""},
        {"content": "x", "name": "a\nb"},
        {"content": "x", "mime_type": "nonsense"},
        {"content": "x", "host": "evil.example.com"},
    ],
    ids=lambda bad: ",".join(sorted(bad)) or "empty",
)
def test_upload_input_is_checked(bad: dict[str, Any]) -> None:
    base: dict[str, Any] = {"name": "a.txt", "mime_type": "text/plain"}
    with pytest.raises(ValidationError):
        DriveFilesUploadInput.model_validate({**base, **bad})


def test_upload_accepts_the_most_a_multipart_upload_takes() -> None:
    DriveFilesUploadInput(name="a", mimeType="text/plain", content="x" * (5 * 1024 * 1024))


async def test_create_sends_json_metadata_so_a_folder_can_be_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder()
    _install(monkeypatch, rec)
    result = await _authed().run(
        {
            "action": "drive_files_create",
            "name": "Reports",
            "mime_type": "application/vnd.google-apps.folder",
            "parents": ["parent123"],
        }
    )
    assert result.success, result.message
    call = rec.last
    assert (call["method"], call["url"].split("?")[0]) == (
        "POST",
        "https://www.googleapis.com/drive/v3/files",
    )
    assert call["json"] == {
        "name": "Reports",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["parent123"],
    }
    assert "supportsAllDrives=true" in call["url"]
    assert "application/octet-stream" not in str(call["headers"])


async def test_update_sends_json_metadata_and_moves_between_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _Recorder()
    _install(monkeypatch, rec)
    result = await _authed().run(
        {
            "action": "drive_files_update",
            "fileid": "file123",
            "name": "renamed.txt",
            "addparents": "new1",
            "removeparents": "old1",
        }
    )
    assert result.success, result.message
    call = rec.last
    assert (call["method"], call["url"].split("?")[0]) == (
        "PATCH",
        "https://www.googleapis.com/drive/v3/files/file123",
    )
    assert call["json"] == {"name": "renamed.txt"}
    assert "addParents=new1" in call["url"] and "removeParents=old1" in call["url"]
    assert "supportsAllDrives=true" in call["url"]


def test_the_hand_written_content_actions_are_present() -> None:
    """Regenerating logic.py and schema.py from the spec would drop these; this fails when it does."""
    assert "drive_files_upload" in GoogleDriveConnector.nw_action_metas()
    assert {"name", "mime_type", "description", "parents"} <= set(
        DriveFilesCreateInput.model_fields
    )
    assert {"name", "mime_type", "description"} <= set(DriveFilesUpdateInput.model_fields)
    assert "body" not in DriveFilesCreateInput.model_fields
