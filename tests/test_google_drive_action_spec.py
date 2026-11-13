#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Google Drive actions map to the right Drive v3 HTTP requests.

Simplified: the connector is OpenAPI-generated (RestConnector), so each action is checked
through a fake shared httpx client instead of patching the Google API client.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

import node_wire_runtime.rest as rest
from node_wire_google_drive.logic import GoogleDriveConnector
from node_wire_google_drive.schema import (
    DriveFilesDeleteInput,
    DriveFilesExportInput,
    DriveFilesGetInput,
    DriveFilesListInput,
    DrivePermissionsCreateInput,
)
from node_wire_runtime import SecretProvider

BASE = "https://www.googleapis.com/drive/v3"


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"GOOGLE_DRIVE_ACCESS_TOKEN": "ya29.dummy"}[key]


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.text = self.content.decode()
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return _Resp(self.payload)


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


def _fake(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> _Client:
    client = _Client(payload)
    _install(monkeypatch, client)
    return client


def test_every_nw_action_is_a_drive_action():
    """The generated connector registers its Drive operations as @nw_action methods."""
    names = set(GoogleDriveConnector.nw_action_metas())
    assert len(names) == 48
    assert all(name.startswith("drive_") for name in names)


@pytest.mark.asyncio
async def test_files_list_maps_query_parameters(monkeypatch):
    client = _fake(monkeypatch, {"files": [{"id": "f1"}]})
    params = DriveFilesListInput(q="trashed = false", pageSize=5)

    result = await _connector().drive_files_list(params, trace_id="t")

    assert result.model_dump()["files"] == [{"id": "f1"}]
    call = client.calls[0]
    assert call["method"] == "GET"
    assert call["url"].startswith(f"{BASE}/files?")
    assert "pageSize=5" in call["url"]
    assert "q=trashed" in call["url"]


@pytest.mark.asyncio
async def test_files_get_puts_file_id_in_the_path(monkeypatch):
    client = _fake(monkeypatch, {"id": "fid-1", "name": "doc.txt"})

    result = await _connector().drive_files_get(DriveFilesGetInput(fileId="fid-1"), trace_id="t")

    assert result.model_dump()["name"] == "doc.txt"
    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["url"] == f"{BASE}/files/fid-1"


@pytest.mark.asyncio
async def test_files_delete_is_a_delete_request(monkeypatch):
    client = _fake(monkeypatch, {})

    await _connector().drive_files_delete(DriveFilesDeleteInput(fileId="fid-99"), trace_id="t")

    assert client.calls[0]["method"] == "DELETE"
    assert client.calls[0]["url"] == f"{BASE}/files/fid-99"


@pytest.mark.asyncio
async def test_files_export_sends_mime_type(monkeypatch):
    client = _fake(monkeypatch, {})
    params = DriveFilesExportInput(fileId="fid-7", mimeType="application/pdf")

    await _connector().drive_files_export(params, trace_id="t")

    assert client.calls[0]["url"].startswith(f"{BASE}/files/fid-7/export?")
    assert "mimeType=application%2Fpdf" in client.calls[0]["url"]


@pytest.mark.asyncio
async def test_permissions_create_sends_json_body(monkeypatch):
    client = _fake(monkeypatch, {"id": "perm-1", "role": "reader"})
    params = DrivePermissionsCreateInput(
        fileId="fid-1", role="reader", type="user", emailAddress="a@example.com"
    )

    result = await _connector().drive_permissions_create(params, trace_id="t")

    assert result.model_dump()["id"] == "perm-1"
    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["url"].startswith(f"{BASE}/files/fid-1/permissions")
    sent = {"role": "reader", "type": "user", "emailAddress": "a@example.com"}
    assert sent.items() <= call["json"].items()


@pytest.mark.asyncio
async def test_run_dispatches_by_action_name(monkeypatch):
    client = _fake(monkeypatch, {"id": "fid-5"})

    response = await _connector().run({"action": "drive_files_get", "fileId": "fid-5"})

    assert response.success is True
    assert response.data is not None and response.data["id"] == "fid-5"
    assert client.calls[0]["url"] == f"{BASE}/files/fid-5"
