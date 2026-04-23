"""
tests/test_auth_providers.py
==============================

Unit tests for the AuthProvider abstraction layer.

Covers:
  - NoAuthProvider
  - StaticTokenAuthProvider (bearer, basic, custom header, refresh)
  - OAuth2AuthProvider (cache hit/miss, expiry, concurrent refresh, 401 refresh,
    private_key_jwt, client_secret_post, missing access_token)
  - ServiceAccountAuthProvider
  - BaseConnector.get_auth_headers() delegation
  - Factory._build_auth_provider() YAML wiring
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from node_wire_runtime.auth import (
    AuthProvider,
    NoAuthProvider,
    OAuth2AuthProvider,
    ServiceAccountAuthProvider,
    StaticTokenAuthProvider,
)
from node_wire_runtime.secrets import SecretProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from pydantic import BaseModel
from node_wire_runtime import BaseConnector, sdk_action

class _Input(BaseModel):
    action: str = "dummy"

class _Output(BaseModel):
    ok: bool = True

class _DummyConnector(BaseConnector):
    connector_id = "test_auth_delegation"
    output_model = _Output

    @sdk_action("dummy")
    async def dummy(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output()

class _NoAuthConnector(BaseConnector):
    connector_id = "test_no_auth_default"
    output_model = _Output

    @sdk_action("x")
    async def x(self, params: _Input, *, trace_id: str) -> _Output:
        return _Output()


class _DictSecretProvider(SecretProvider):
    def __init__(self, data: dict) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        if key not in self._data:
            from node_wire_runtime.secrets import SecretNotFoundError
            raise SecretNotFoundError(key)
        return self._data[key]


def _token_response(access_token: str = "tok-abc", expires_in: int = 3600) -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"access_token": access_token, "expires_in": expires_in}
    return m


# ---------------------------------------------------------------------------
# NoAuthProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_auth_returns_empty_headers() -> None:
    provider = NoAuthProvider()
    assert await provider.get_headers() == {}


@pytest.mark.asyncio
async def test_no_auth_returns_none_credentials() -> None:
    provider = NoAuthProvider()
    assert await provider.get_client_credentials() is None


@pytest.mark.asyncio
async def test_no_auth_refresh_is_noop() -> None:
    provider = NoAuthProvider()
    await provider.refresh()  # must not raise


# ---------------------------------------------------------------------------
# StaticTokenAuthProvider
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_static_token_bearer_header() -> None:
    sp = _DictSecretProvider({"MY_KEY": "secret-value"})
    provider = StaticTokenAuthProvider(secret_provider=sp, secret_key="MY_KEY")
    headers = await provider.get_headers()
    assert headers == {"Authorization": "Bearer secret-value"}


@pytest.mark.asyncio
async def test_static_token_custom_header_and_prefix() -> None:
    sp = _DictSecretProvider({"api_key": "abc123"})
    provider = StaticTokenAuthProvider(
        secret_provider=sp,
        secret_key="api_key",
        header_name="X-Api-Key",
        prefix="",
    )
    headers = await provider.get_headers()
    assert headers == {"X-Api-Key": "abc123"}


@pytest.mark.asyncio
async def test_static_token_base64_encoding() -> None:
    import base64
    sp = _DictSecretProvider({"creds": "user:pass"})
    provider = StaticTokenAuthProvider(
        secret_provider=sp,
        secret_key="creds",
        prefix="Basic",
        encoding="base64",
    )
    headers = await provider.get_headers()
    expected = base64.b64encode(b"user:pass").decode()
    assert headers["Authorization"] == f"Basic {expected}"


@pytest.mark.asyncio
async def test_static_token_cached_after_first_call() -> None:
    """Secret provider is called only once; result is cached."""
    sp = _DictSecretProvider({"k": "val"})
    provider = StaticTokenAuthProvider(secret_provider=sp, secret_key="k")
    h1 = await provider.get_headers()
    h2 = await provider.get_headers()
    assert h1 == h2


@pytest.mark.asyncio
async def test_static_token_refresh_clears_cache() -> None:
    """After refresh(), the next call rebuilds the header."""
    calls = []
    class _Counting(SecretProvider):
        def get_secret(self, key: str) -> str:
            calls.append(key)
            return "val"

    provider = StaticTokenAuthProvider(secret_provider=_Counting(), secret_key="k")
    await provider.get_headers()
    await provider.refresh()
    await provider.get_headers()
    assert len(calls) == 2  # resolved twice — once per cache population


# ---------------------------------------------------------------------------
# OAuth2AuthProvider — token caching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oauth2_token_cache_hit() -> None:
    """A second call within TTL must NOT issue another HTTP request."""
    sp = _DictSecretProvider({
        "client_id": "cid",
        "token_url": "https://auth.example.com/token",
        "private_key": "---fake-key---",
        "kid": "kid1",
    })
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="private_key_jwt",
        token_url_secret="token_url",
        client_id_secret="client_id",
        private_key_secret="private_key",
        kid_secret="kid",
    )

    with patch("node_wire_runtime.auth.oauth2.OAuth2AuthProvider._fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"access_token": "tok-1", "expires_in": 3600}
        h1 = await provider.get_headers()
        h2 = await provider.get_headers()

    assert mock_fetch.call_count == 1
    assert h1["Authorization"] == "Bearer tok-1"
    assert h2["Authorization"] == "Bearer tok-1"


@pytest.mark.asyncio
async def test_oauth2_token_cache_miss_on_expiry() -> None:
    """An expired token must trigger a new fetch."""
    sp = _DictSecretProvider({"client_id": "x", "token_url": "http://t"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",  # dummy
        buffer_secs=0,
    )

    with patch("node_wire_runtime.auth.oauth2.OAuth2AuthProvider._fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"access_token": "tok-a", "expires_in": 1}
        await provider.get_headers()
        # Force expiry
        provider._expires_at = time.monotonic() - 1

        mock_fetch.return_value = {"access_token": "tok-b", "expires_in": 3600}
        h2 = await provider.get_headers()

    assert mock_fetch.call_count == 2
    assert h2["Authorization"] == "Bearer tok-b"


@pytest.mark.asyncio
async def test_oauth2_refresh_clears_cache() -> None:
    """Calling refresh() forces a new fetch on the next get_headers()."""
    sp = _DictSecretProvider({"client_id": "x", "token_url": "http://t"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
    )

    with patch("node_wire_runtime.auth.oauth2.OAuth2AuthProvider._fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"access_token": "tok-1", "expires_in": 3600}
        await provider.get_headers()  # populates cache

        await provider.refresh()      # invalidates cache

        mock_fetch.return_value = {"access_token": "tok-2", "expires_in": 3600}
        h2 = await provider.get_headers()  # must re-fetch

    assert mock_fetch.call_count == 2
    assert h2["Authorization"] == "Bearer tok-2"


@pytest.mark.asyncio
async def test_oauth2_concurrent_refresh_single_fetch() -> None:
    """Concurrent get_headers() calls must result in exactly one HTTP fetch (Lock)."""
    sp = _DictSecretProvider({"client_id": "x", "token_url": "http://t"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
    )
    fetch_count = 0

    async def _fake_fetch() -> dict:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0)  # yield to allow other coroutines to race
        return {"access_token": "tok-concurrent", "expires_in": 3600}

    with patch.object(provider, "_fetch_token", side_effect=_fake_fetch):
        results = await asyncio.gather(*[provider.get_headers() for _ in range(10)])

    assert fetch_count == 1  # exactly one HTTP call despite 10 concurrent waiters
    assert all(r["Authorization"] == "Bearer tok-concurrent" for r in results)


@pytest.mark.asyncio
async def test_oauth2_401_retry_via_refresh() -> None:
    """Simulates: connector receives 401 → calls refresh() → next request gets fresh token."""
    sp = _DictSecretProvider({"client_id": "x", "token_url": "http://t"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
    )

    with patch("node_wire_runtime.auth.oauth2.OAuth2AuthProvider._fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"access_token": "old-token", "expires_in": 3600}
        h1 = await provider.get_headers()
        assert h1["Authorization"] == "Bearer old-token"

        # Simulate 401 — connector calls refresh()
        await provider.refresh()

        mock_fetch.return_value = {"access_token": "new-token", "expires_in": 3600}
        h2 = await provider.get_headers()
        assert h2["Authorization"] == "Bearer new-token"
        assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_oauth2_missing_access_token_raises() -> None:
    """A token response without access_token must raise ValueError."""
    sp = _DictSecretProvider({"client_id": "x", "token_url": "http://t"})
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_id",
    )
    with patch("node_wire_runtime.auth.oauth2.OAuth2AuthProvider._fetch_token", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = {"token_type": "bearer"}  # no access_token key
        with pytest.raises(ValueError, match="access_token"):
            await provider.get_headers()


@pytest.mark.asyncio
async def test_base_connector_delegates_to_auth_provider(tmp_path: Any) -> None:
    """get_auth_headers() returns the provider's headers dict."""
    sp = _DictSecretProvider({"MY_API_KEY": "secret-123"})
    auth = StaticTokenAuthProvider(secret_provider=sp, secret_key="MY_API_KEY")
    connector = _DummyConnector(secret_provider=sp, auth_provider=auth)
    headers = await connector.get_auth_headers()
    assert headers == {"Authorization": "Bearer secret-123"}


@pytest.mark.asyncio
async def test_base_connector_no_provider_defaults_to_no_auth(tmp_path: Any) -> None:
    """A connector with no auth_provider returns {} from get_auth_headers()."""
    connector = _NoAuthConnector()  # no auth_provider kwarg
    assert await connector.get_auth_headers() == {}


# ---------------------------------------------------------------------------
# Factory._build_auth_provider()
# ---------------------------------------------------------------------------

def test_factory_defaults_to_no_auth_when_auth_block_absent() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.auth import NoAuthProvider

    sp = _DictSecretProvider({})
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp
    factory._configs = {}
    factory._connectors = {}

    provider = factory._build_auth_provider("test_connector", {})
    assert isinstance(provider, NoAuthProvider)


def test_factory_builds_static_token_provider() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.auth import StaticTokenAuthProvider

    sp = _DictSecretProvider({"my_api_key": "abc"})
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp

    cfg = {"auth": {"provider": "static_token", "secret_key": "my_api_key", "prefix": ""}}
    provider = factory._build_auth_provider("stripe", cfg)
    assert isinstance(provider, StaticTokenAuthProvider)


def test_factory_builds_oauth2_provider() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.auth import OAuth2AuthProvider

    sp = _DictSecretProvider({})
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp

    cfg = {
        "auth": {
            "provider": "oauth2",
            "grant_method": "private_key_jwt",
            "token_url_secret": "epic_token_url",
            "client_id_secret": "epic_client_id",
            "private_key_secret": "epic_private_key",
            "kid_secret": "epic_kid",
        }
    }
    provider = factory._build_auth_provider("fhir_epic", cfg)
    assert isinstance(provider, OAuth2AuthProvider)


def test_factory_builds_service_account_provider() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.auth import ServiceAccountAuthProvider

    sp = _DictSecretProvider({})
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp

    cfg = {
        "auth": {
            "provider": "service_account",
            "sa_json_secret": "GOOGLE_DRIVE_SA_JSON",
        }
    }
    provider = factory._build_auth_provider("google_drive", cfg)
    assert isinstance(provider, ServiceAccountAuthProvider)
