#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Auth providers read network-backed secrets (``blocking_io``) off the event loop."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from node_wire_runtime.auth.apikey_query import ApiKeyQueryAuthProvider
from node_wire_runtime.auth.oauth2 import OAuth2AuthProvider
from node_wire_runtime.auth.service_account import ServiceAccountAuthProvider
from node_wire_runtime.secrets.base import SecretProvider

_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)


class _Recording(SecretProvider):
    def __init__(self, blocking: bool) -> None:
        self.blocking_io = blocking
        self.threads: list[str] = []

    def get_secret(self, key: str) -> str:
        self.threads.append(threading.current_thread().name)
        if key == "PRIVATE_KEY":
            return _PEM
        if key == "TOKEN_URL":
            return "https://idp.example/token"
        if key == "SA_JSON":
            return "/nonexistent/service-account.json"
        return f"value-{key}"


def _oauth2(sp: SecretProvider, grant: str) -> OAuth2AuthProvider:
    extra: Dict[str, Any] = {
        "private_key_jwt": {"private_key_secret": "PRIVATE_KEY", "kid_secret": "KID"},
        "client_secret_post": {"client_secret_secret": "CLIENT_SECRET"},
        "refresh_token": {
            "client_secret_secret": "CLIENT_SECRET",
            "refresh_token_secret": "REFRESH",
        },
    }[grant]
    return OAuth2AuthProvider(
        secret_provider=sp,
        grant_method=grant,
        token_url_secret="TOKEN_URL",
        client_id_secret="CLIENT_ID",
        scopes_secret="SCOPES",
        **extra,
    )


@pytest.mark.parametrize("grant", ["private_key_jwt", "client_secret_post", "refresh_token"])
@pytest.mark.parametrize("blocking", [True, False])
def test_oauth2_secret_reads_follow_blocking_io(
    grant: str, blocking: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: Dict[str, Any] = {}

    async def _fake_post(token_url: str, data: Dict[str, str]) -> Dict[str, Any]:
        sent["url"], sent["data"] = token_url, data
        return {"access_token": "at", "expires_in": 3600}

    monkeypatch.setattr(OAuth2AuthProvider, "_post_token", staticmethod(_fake_post))
    sp = _Recording(blocking)
    main = threading.current_thread().name

    headers = asyncio.run(_oauth2(sp, grant).get_headers())

    assert headers["Authorization"] == "Bearer at"
    assert sent["url"] == "https://idp.example/token"
    assert sent["data"]["scope"] == "value-SCOPES"
    assert sp.threads, "provider was never read"
    if blocking:
        assert main not in sp.threads
    else:
        assert set(sp.threads) == {main}


def test_oauth2_refresh_prefers_rotated_override(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Dict[str, str]] = []

    async def _fake_post(token_url: str, data: Dict[str, str]) -> Dict[str, Any]:
        sent.append(data)
        return {"access_token": "at", "expires_in": 0, "refresh_token": "rotated"}

    monkeypatch.setattr(OAuth2AuthProvider, "_post_token", staticmethod(_fake_post))
    provider = _oauth2(_Recording(True), "refresh_token")

    async def _twice() -> None:
        await provider.get_headers()
        await provider.get_headers()

    asyncio.run(_twice())
    assert [d["refresh_token"] for d in sent] == ["value-REFRESH", "rotated"]


@pytest.mark.parametrize("blocking", [True, False])
def test_apikey_query_secret_read_follows_blocking_io(blocking: bool) -> None:
    sp = _Recording(blocking)
    main = threading.current_thread().name
    auth = ApiKeyQueryAuthProvider(secret_provider=sp, secret_key="API_KEY", name="key")

    assert asyncio.run(auth.get_query_params()) == {"key": "value-API_KEY"}
    assert (main not in sp.threads) if blocking else (sp.threads == [main])


@pytest.mark.parametrize("blocking", [True, False])
def test_service_account_secret_read_follows_blocking_io(blocking: bool) -> None:
    pytest.importorskip("google.oauth2.service_account")
    sp = _Recording(blocking)
    main = threading.current_thread().name
    auth = ServiceAccountAuthProvider(secret_provider=sp, sa_json_secret="SA_JSON")

    with pytest.raises(ValueError, match="service account file not found"):
        asyncio.run(auth.get_client_credentials())
    assert (main not in sp.threads) if blocking else (sp.threads == [main])
