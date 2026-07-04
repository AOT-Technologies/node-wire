#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Additional coverage for OAuth2AuthProvider and ServiceAccountAuthProvider."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from node_wire_runtime.auth import OAuth2AuthProvider, ServiceAccountAuthProvider
from node_wire_runtime.secrets import SecretProvider
from node_wire_runtime.secrets.base import SecretNotFoundError


class _DictSecretProvider(SecretProvider):
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        if key not in self._data:
            raise SecretNotFoundError(key)
        return self._data[key]


def _oauth2_client_secret_provider(**extra: str) -> _DictSecretProvider:
    data = {
        "token_url": "https://idp.example.com/token",
        "client_id": "my-client",
        "client_secret": "super-secret",
        **extra,
    }
    return _DictSecretProvider(data)


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — invalid grant method
# ---------------------------------------------------------------------------


def test_oauth2_invalid_grant_method_raises() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    with pytest.raises(ValueError, match="Unsupported grant_method"):
        OAuth2AuthProvider(
            secret_provider=sp,
            grant_method="magic",
            token_url_secret="token_url",
            client_id_secret="client_id",
        )


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — _resolve_scopes via scopes_secret
# ---------------------------------------------------------------------------


def test_resolve_scopes_from_secret() -> None:
    sp = _DictSecretProvider({
        "token_url": "x",
        "client_id": "y",
        "scope_val": "openid profile",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",  # not used in resolve_scopes
        scopes_secret="scope_val",
    )
    assert provider._resolve_scopes() == "openid profile"


def test_resolve_scopes_falls_back_to_static_when_secret_missing() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
        scopes_secret="missing_key",
        scopes=["read", "write"],
    )
    assert provider._resolve_scopes() == "read write"


def test_resolve_scopes_returns_none_when_none_configured() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
    )
    assert provider._resolve_scopes() is None


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — client_secret_post grant
# ---------------------------------------------------------------------------


async def test_oauth2_client_secret_post_success() -> None:
    sp = _oauth2_client_secret_provider()
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_secret",
        scopes=["read"],
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "tok-cs", "expires_in": 3600}

    with patch("node_wire_runtime.auth.oauth2.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = MagicMock(return_value=mock_client)
        mock_client.__aexit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)
        # Make post awaitable
        import asyncio
        mock_client.post = MagicMock(side_effect=lambda *a, **kw: mock_response)

        async def _fake_enter(_: Any) -> Any:
            return mock_client

        async def _fake_exit(*_: Any) -> bool:
            return False

        mock_client.__aenter__ = _fake_enter
        mock_client.__aexit__ = _fake_exit
        mock_client_cls.return_value = mock_client

        # Use respx-style: patch the _post_token static method directly
        with patch.object(
            OAuth2AuthProvider,
            "_post_token",
            return_value={"access_token": "tok-cs", "expires_in": 3600},
        ):
            headers = await provider.get_headers()

    assert headers["Authorization"] == "Bearer tok-cs"


async def test_client_secret_post_missing_secret_raises() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        # client_secret_secret deliberately absent
    )
    with pytest.raises(ValueError, match="client_secret_secret"):
        await provider._fetch_client_secret_post()


async def test_oauth2_client_secret_post_with_scope() -> None:
    sp = _oauth2_client_secret_provider()
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_secret",
        scopes=["openid"],
    )
    captured_data: list[dict] = []

    async def fake_post_token(url: str, data: dict) -> dict:
        captured_data.append(data)
        return {"access_token": "tok2", "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post_token):
        await provider.get_headers()

    assert captured_data[0]["scope"] == "openid"
    assert captured_data[0]["grant_type"] == "client_credentials"


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — refresh_token grant
# ---------------------------------------------------------------------------


async def test_oauth2_refresh_token_success() -> None:
    sp = _DictSecretProvider({
        "token_url": "https://idp.example.com/token",
        "client_id": "my-client",
        "client_secret": "sec",
        "refresh_token": "rt-123",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="refresh_token",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_secret",
        refresh_token_secret="refresh_token",
        scopes=["profile"],
    )
    captured: list[dict] = []

    async def fake_post(url: str, data: dict) -> dict:
        captured.append(data)
        return {"access_token": "new-tok", "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        headers = await provider.get_headers()

    assert headers["Authorization"] == "Bearer new-tok"
    assert captured[0]["grant_type"] == "refresh_token"
    assert captured[0]["refresh_token"] == "rt-123"
    assert captured[0]["client_secret"] == "sec"
    assert captured[0]["scope"] == "profile"


async def test_oauth2_refresh_token_without_client_secret() -> None:
    sp = _DictSecretProvider({
        "token_url": "https://idp.example.com/token",
        "client_id": "my-client",
        "refresh_token": "rt-456",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="refresh_token",
        token_url_secret="token_url",
        client_id_secret="client_id",
        refresh_token_secret="refresh_token",
    )
    captured: list[dict] = []

    async def fake_post(url: str, data: dict) -> dict:
        captured.append(data)
        return {"access_token": "no-secret-tok", "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        await provider.get_headers()

    assert "client_secret" not in captured[0]


async def test_oauth2_refresh_token_missing_secret_raises() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="refresh_token",
        token_url_secret="token_url",
        client_id_secret="client_id",
        # refresh_token_secret absent
    )
    with pytest.raises(ValueError, match="refresh_token_secret"):
        await provider._fetch_refresh_token()


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — private_key_jwt grant
# ---------------------------------------------------------------------------


async def test_oauth2_private_key_jwt_invalid_key_raises() -> None:
    sp = _DictSecretProvider({
        "token_url": "https://idp.example.com/token",
        "client_id": "client",
        "private_key": "not-a-valid-pem-key",
        "kid": "key-1",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="private_key_jwt",
        token_url_secret="token_url",
        client_id_secret="client_id",
        private_key_secret="private_key",
        kid_secret="kid",
        algorithm="RS384",
    )
    with pytest.raises(ValueError, match="private_key_jwt"):
        await provider._fetch_private_key_jwt()


async def test_oauth2_private_key_jwt_missing_secrets_raises() -> None:
    sp = _DictSecretProvider({"token_url": "x", "client_id": "y"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="private_key_jwt",
        token_url_secret="token_url",
        client_id_secret="client_id",
        # private_key_secret and kid_secret absent
    )
    with pytest.raises(ValueError, match="private_key_secret.*kid_secret"):
        await provider._fetch_private_key_jwt()


async def test_oauth2_private_key_jwt_success_with_rsa_key() -> None:
    """Test private_key_jwt success path with a real RSA key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    sp = _DictSecretProvider({
        "token_url": "https://idp.example.com/token",
        "client_id": "client-id",
        "private_key": private_pem,
        "kid": "rsa-key-1",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="private_key_jwt",
        token_url_secret="token_url",
        client_id_secret="client_id",
        private_key_secret="private_key",
        kid_secret="kid",
        algorithm="RS384",
        scopes=["system/*.read"],
    )
    captured: list[dict] = []

    async def fake_post(url: str, data: dict) -> dict:
        captured.append(data)
        return {"access_token": "pkey-tok", "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        headers = await provider.get_headers()

    assert headers["Authorization"] == "Bearer pkey-tok"
    assert captured[0]["grant_type"] == "client_credentials"
    assert "client_assertion" in captured[0]
    assert captured[0]["scope"] == "system/*.read"


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — _post_token non-200 error
# ---------------------------------------------------------------------------


async def test_post_token_non_200_raises() -> None:
    transport = httpx.MockTransport(
        handler=lambda req: httpx.Response(401, text="Unauthorized")
    )

    with patch(
        "node_wire_runtime.auth.oauth2.httpx.AsyncClient",
        return_value=httpx.AsyncClient(transport=transport),
    ):
        with pytest.raises(ValueError, match="HTTP 401"):
            await OAuth2AuthProvider._post_token(
                "https://fake.example.com/token",
                {"grant_type": "client_credentials"},
            )


# ---------------------------------------------------------------------------
# ServiceAccountAuthProvider
# ---------------------------------------------------------------------------


async def test_sa_get_headers_returns_empty() -> None:
    sp = _DictSecretProvider({"sa_json": '{"type": "service_account"}'})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")
    headers = await provider.get_headers()
    assert headers == {}


async def test_sa_get_client_credentials_caches() -> None:
    fake_creds = MagicMock()
    sp = _DictSecretProvider({"sa_json": json.dumps({"type": "service_account"})})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    with patch.object(provider, "_build_credentials", return_value=fake_creds) as mock_build:
        creds1 = await provider.get_client_credentials()
        creds2 = await provider.get_client_credentials()

    assert creds1 is fake_creds
    assert creds2 is fake_creds
    mock_build.assert_called_once()


async def test_sa_refresh_clears_credentials() -> None:
    fake_creds = MagicMock()
    sp = _DictSecretProvider({"sa_json": "{}"})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")
    provider._credentials = fake_creds

    await provider.refresh()
    assert provider._credentials is None


def test_sa_build_credentials_from_json_string() -> None:
    import json as _json

    fake_creds = MagicMock()
    sa_info = {
        "type": "service_account",
        "project_id": "proj",
        "private_key_id": "key1",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
        "client_email": "svc@proj.iam.gserviceaccount.com",
        "client_id": "12345",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    sp = _DictSecretProvider({"sa_json": _json.dumps(sa_info)})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_info",
        return_value=fake_creds,
    ):
        creds = provider._build_credentials()
    assert creds is fake_creds


def test_sa_build_credentials_from_file_path(tmp_path: Any) -> None:
    import json as _json

    sa_info = {
        "type": "service_account",
        "project_id": "proj",
        "private_key_id": "key1",
        "private_key": "fake-key",
        "client_email": "svc@proj.iam.gserviceaccount.com",
        "client_id": "12345",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(_json.dumps(sa_info))

    fake_creds = MagicMock()
    sp = _DictSecretProvider({"sa_json": str(sa_file)})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    with patch(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        return_value=fake_creds,
    ):
        creds = provider._build_credentials()
    assert creds is fake_creds


def test_sa_build_credentials_file_not_found(tmp_path: Any) -> None:
    sp = _DictSecretProvider({"sa_json": "/nonexistent/path/sa.json"})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    with pytest.raises(ValueError, match="not found at path"):
        provider._build_credentials()


def test_sa_build_credentials_invalid_json_info() -> None:
    import json as _json

    bad_info = {"type": "service_account"}  # missing required fields
    sp = _DictSecretProvider({"sa_json": _json.dumps(bad_info)})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    with pytest.raises((ValueError, Exception)):
        provider._build_credentials()


def test_sa_build_credentials_google_auth_not_installed() -> None:
    import sys

    sp = _DictSecretProvider({"sa_json": '{"type": "service_account"}'})
    provider = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="sa_json")

    original = sys.modules.get("google.oauth2")
    original_sa = sys.modules.get("google.oauth2.service_account")
    try:
        sys.modules["google.oauth2"] = None  # type: ignore[assignment]
        sys.modules["google.oauth2.service_account"] = None  # type: ignore[assignment]
        with pytest.raises(ImportError, match="google-auth"):
            provider._build_credentials()
    finally:
        if original is None:
            sys.modules.pop("google.oauth2", None)
        else:
            sys.modules["google.oauth2"] = original
        if original_sa is None:
            sys.modules.pop("google.oauth2.service_account", None)
        else:
            sys.modules["google.oauth2.service_account"] = original_sa
