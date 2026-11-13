#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Google Drive authentication: the credential reaches Drive as an HTTP header.

Simplified: the generated connector is a RestConnector with no client to build, so what
matters is that the configured auth provider's headers are sent with every Drive call.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

import node_wire_runtime.rest as rest
from node_wire_google_drive.logic import GoogleDriveConnector
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth import StaticTokenAuthProvider
from node_wire_runtime.auth.no_auth import NoAuthProvider


class MockSecretProvider(SecretProvider):
    def __init__(self, token: str = "ya29.dummy") -> None:
        self._token = token

    def get_secret(self, key: str) -> str:
        if key == "GOOGLE_DRIVE_ACCESS_TOKEN":
            return self._token
        raise KeyError(key)


class _Resp:
    status_code = 200
    content = json.dumps({"id": "fid"}).encode()
    text = content.decode()
    headers = {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return {"id": "fid"}

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        return _Resp()


class _ClientContext:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def __aenter__(self) -> Any:
        return self._client

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _install(monkeypatch: pytest.MonkeyPatch) -> _Client:
    """Route the runtime's HTTP calls to a fake client (pooled-client and per-call runtimes)."""
    client = _Client()
    monkeypatch.setattr(rest, "assert_safe_destination", AsyncMock(return_value=None))
    if hasattr(rest, "_shared_client"):
        monkeypatch.setattr(rest, "_shared_client", lambda _trust_env: client)
    else:
        monkeypatch.setattr(rest.httpx, "AsyncClient", lambda **_kw: _ClientContext(client))
    return client


def _connector(
    auth_provider: object, secrets: SecretProvider | None = None
) -> GoogleDriveConnector:
    return GoogleDriveConnector(
        secret_provider=secrets or MockSecretProvider(), auth_provider=auth_provider
    )


async def _get_file(connector: GoogleDriveConnector) -> None:
    result = await connector.run({"action": "drive_files_get", "fileId": "fid"})
    assert result.success is True


@pytest.mark.asyncio
async def test_static_token_is_sent_as_a_bearer_header(monkeypatch):
    client = _install(monkeypatch)
    secrets = MockSecretProvider("ya29.abc")
    auth = StaticTokenAuthProvider(secret_provider=secrets, secret_key="GOOGLE_DRIVE_ACCESS_TOKEN")

    await _get_file(_connector(auth, secrets))

    assert client.calls[0]["headers"]["Authorization"] == "Bearer ya29.abc"


@pytest.mark.asyncio
async def test_token_is_read_once_when_cached(monkeypatch):
    client = _install(monkeypatch)
    secrets = MockSecretProvider("ya29.first")
    auth = StaticTokenAuthProvider(secret_provider=secrets, secret_key="GOOGLE_DRIVE_ACCESS_TOKEN")
    connector = _connector(auth, secrets)

    await _get_file(connector)
    secrets._token = "ya29.rotated"
    await _get_file(connector)

    assert [c["headers"]["Authorization"] for c in client.calls] == ["Bearer ya29.first"] * 2


@pytest.mark.asyncio
async def test_uncached_token_picks_up_rotation(monkeypatch):
    client = _install(monkeypatch)
    secrets = MockSecretProvider("ya29.first")
    auth = StaticTokenAuthProvider(
        secret_provider=secrets, secret_key="GOOGLE_DRIVE_ACCESS_TOKEN", cache=False
    )
    connector = _connector(auth, secrets)

    await _get_file(connector)
    secrets._token = "ya29.rotated"
    await _get_file(connector)

    assert [c["headers"]["Authorization"] for c in client.calls] == [
        "Bearer ya29.first",
        "Bearer ya29.rotated",
    ]


@pytest.mark.asyncio
async def test_no_auth_provider_sends_no_authorization_header(monkeypatch):
    client = _install(monkeypatch)

    await _get_file(_connector(NoAuthProvider()))

    assert "Authorization" not in client.calls[0]["headers"]
