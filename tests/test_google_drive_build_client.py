#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from node_wire_google_drive.logic import GoogleDriveConnector
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth.no_auth import NoAuthProvider

_DRIVE_SCOPE = ["https://www.googleapis.com/auth/drive"]


class MockSecretProvider(SecretProvider):
    def __init__(self, *, sa_json: str | None = None) -> None:
        self._sa_json = sa_json or '{"type":"service_account","project_id":"dummy"}'

    def get_secret(self, key: str) -> str:
        if key == "GOOGLE_DRIVE_SA_JSON":
            return self._sa_json
        raise KeyError(key)


def _connector(
    *,
    auth_provider: object | None = None,
    secret_provider: SecretProvider | None = None,
) -> GoogleDriveConnector:
    return GoogleDriveConnector(
        secret_provider=secret_provider or MockSecretProvider(),
        auth_provider=auth_provider or NoAuthProvider(),
    )


def _auth_provider_with_creds(fake_creds: object) -> MagicMock:
    provider = MagicMock()
    provider.get_client_credentials = AsyncMock(return_value=fake_creds)
    return provider


@contextmanager
def _patch_build():
    with patch(
        "node_wire_google_drive.logic.build", return_value=MagicMock(name="drive")
    ) as mock_build:
        yield mock_build


def test_build_client_uses_auth_provider_on_idle_event_loop() -> None:
    fake_creds = object()
    connector = _connector(auth_provider=_auth_provider_with_creds(fake_creds))
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = False
    mock_loop.run_until_complete.return_value = fake_creds

    with (
        patch("asyncio.get_event_loop", return_value=mock_loop),
        _patch_build() as mock_build,
    ):
        client = connector.build_client()

    assert client is mock_build.return_value
    mock_loop.run_until_complete.assert_called_once()
    mock_build.assert_called_once_with("drive", "v3", credentials=fake_creds)


@pytest.mark.asyncio
async def test_build_client_uses_auth_provider_when_event_loop_is_running() -> None:
    fake_creds = object()
    connector = _connector(auth_provider=_auth_provider_with_creds(fake_creds))

    with _patch_build() as mock_build:
        client = connector.build_client()

    assert client is mock_build.return_value
    mock_build.assert_called_once_with("drive", "v3", credentials=fake_creds)
    connector.auth_provider.get_client_credentials.assert_awaited_once()


def test_build_client_uses_asyncio_run_when_get_event_loop_raises() -> None:
    fake_creds = object()
    connector = _connector(auth_provider=_auth_provider_with_creds(fake_creds))

    with (
        patch("asyncio.get_event_loop", side_effect=RuntimeError("no loop")),
        patch("asyncio.run", return_value=fake_creds) as mock_run,
        _patch_build() as mock_build,
    ):
        client = connector.build_client()

    assert client is mock_build.return_value
    mock_run.assert_called_once()
    mock_build.assert_called_once_with("drive", "v3", credentials=fake_creds)


def test_build_client_falls_back_to_inline_sa_json_when_auth_returns_none() -> None:
    connector = _connector()
    json_creds = MagicMock(name="json_creds")
    sa_info = {"type": "service_account", "project_id": "dummy"}

    with (
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value=json_creds,
        ) as mock_from_info,
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_file"
        ) as mock_from_file,
        _patch_build() as mock_build,
    ):
        client = connector.build_client()

    assert client is mock_build.return_value
    mock_from_info.assert_called_once_with(sa_info, scopes=_DRIVE_SCOPE)
    mock_from_file.assert_not_called()
    mock_build.assert_called_once_with("drive", "v3", credentials=json_creds)


def test_build_client_falls_back_to_sa_file_path_when_json_parse_fails() -> None:
    connector = _connector(secret_provider=MockSecretProvider(sa_json="/path/to/sa.json"))
    file_creds = MagicMock(name="file_creds")

    with (
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
        ) as mock_from_info,
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_file",
            return_value=file_creds,
        ) as mock_from_file,
        _patch_build() as mock_build,
    ):
        client = connector.build_client()

    assert client is mock_build.return_value
    mock_from_info.assert_not_called()
    mock_from_file.assert_called_once_with("/path/to/sa.json", scopes=_DRIVE_SCOPE)
    mock_build.assert_called_once_with("drive", "v3", credentials=file_creds)


def test_get_client_caches_build_client_result() -> None:
    connector = _connector()
    mock_drive = MagicMock(name="drive")

    with patch.object(connector, "build_client", return_value=mock_drive) as mock_build_client:
        first = connector.get_client()
        second = connector.get_client()

    assert first is mock_drive
    assert second is mock_drive
    mock_build_client.assert_called_once()
