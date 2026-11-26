#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""OAuth2AuthProvider cache lifecycle: expiry buffer, targeted refresh, rejected override."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from node_wire_runtime.auth import OAuth2AuthProvider
from node_wire_runtime.secrets import SecretProvider
from node_wire_runtime.secrets.base import SecretNotFoundError


class _DictSecretProvider(SecretProvider):
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        if key not in self._data:
            raise SecretNotFoundError(key)
        return self._data[key]


def _client_credentials_provider(**kwargs) -> OAuth2AuthProvider:
    sp = _DictSecretProvider(
        {"token_url": "https://idp.example.com/token", "client_id": "c", "client_secret": "s"}
    )
    return OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="client_secret_post",
        token_url_secret="token_url",
        client_id_secret="client_id",
        client_secret_secret="client_secret",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_short_lived_token_is_reused_when_buffer_exceeds_lifetime() -> None:
    """expires_in=30 with the default 60 s buffer used to refetch on every call."""
    provider = _client_credentials_provider()
    calls = 0

    async def fake_post(url: str, data: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"access_token": f"tok{calls}", "expires_in": 30}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        first = await provider.get_headers()
        second = await provider.get_headers()

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_refresh_with_stale_rejected_token_keeps_newer_cache() -> None:
    provider = _client_credentials_provider()
    tokens = iter(["tok1", "tok2", "tok3"])

    async def fake_post(url: str, data: dict) -> dict:
        return {"access_token": next(tokens), "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        await provider.get_headers()  # tok1
        await provider.refresh("tok1")  # tok1 rejected -> invalidated
        assert (await provider.get_headers())["Authorization"] == "Bearer tok2"
        # A late caller reports tok1 rejected again: tok2 must survive.
        await provider.refresh("tok1")
        assert (await provider.get_headers())["Authorization"] == "Bearer tok2"
        # No-arg refresh still invalidates unconditionally.
        await provider.refresh()
        assert (await provider.get_headers())["Authorization"] == "Bearer tok3"


@pytest.mark.asyncio
async def test_rejected_refresh_token_override_is_cleared() -> None:
    """When the IdP rejects the in-memory rotated token, fall back to the secret store
    (an operator may have re-seeded it) instead of replaying the dead token forever."""
    sp = _DictSecretProvider(
        {"token_url": "https://idp.example.com/token", "client_id": "c", "refresh_token": "rt-a"}
    )
    provider = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="refresh_token",
        token_url_secret="token_url",
        client_id_secret="client_id",
        refresh_token_secret="refresh_token",
    )
    seen: list[str] = []

    async def fake_post(url: str, data: dict) -> dict:
        seen.append(data["refresh_token"])
        if data["refresh_token"] == "rt-rotated":
            raise ValueError("OAuth2 token request failed (HTTP 400)")
        if len(seen) == 1:
            return {"access_token": "t1", "expires_in": 0, "refresh_token": "rt-rotated"}
        return {"access_token": "t2", "expires_in": 3600}

    with patch.object(OAuth2AuthProvider, "_post_token", side_effect=fake_post):
        await provider.get_headers()
        sp._data["refresh_token"] = "rt-reseeded"
        with pytest.raises(ValueError):
            await provider.get_headers()
        headers = await provider.get_headers()

    assert seen == ["rt-a", "rt-rotated", "rt-reseeded"]
    assert headers["Authorization"] == "Bearer t2"
