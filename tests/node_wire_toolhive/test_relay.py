# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""RelayAuthProvider placement per provider type, and the opt-in hook."""

from __future__ import annotations

import pytest

from node_wire_runtime.auth.base import reset_upstream_bearer, set_upstream_bearer
from node_wire_toolhive.relay import (
    MissingUpstreamTokenError,
    RelayAuthProvider,
    relay_auth_provider_hook,
)


async def _with_token(token: str | None, provider: RelayAuthProvider):
    ctx = set_upstream_bearer(token)
    try:
        return await provider.get_headers(), await provider.get_query_params()
    finally:
        reset_upstream_bearer(ctx)


@pytest.mark.parametrize("provider", ["oauth2", "service_account", "upstream_bearer"])
async def test_oauth_style_schemes_become_bearer(provider: str) -> None:
    headers, query = await _with_token("t", RelayAuthProvider({"provider": provider}))
    assert headers == {"Authorization": "Bearer t"}
    assert query == {}


async def test_static_token_keeps_encoding() -> None:
    auth = {"provider": "static_token", "secret_key": "X", "prefix": "Basic", "encoding": "base64"}
    headers, _ = await _with_token("user:pass", RelayAuthProvider(auth))
    assert headers == {"Authorization": "Basic dXNlcjpwYXNz"}


async def test_no_token_raises() -> None:
    provider = RelayAuthProvider({"provider": "apikey_query", "name": "k", "secret_key": "X"})
    ctx = set_upstream_bearer(None)
    try:
        with pytest.raises(MissingUpstreamTokenError):
            await provider.get_query_params()
    finally:
        reset_upstream_bearer(ctx)


def test_unsupported_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="static_credentials"):
        RelayAuthProvider({"provider": "static_credentials"})


def test_hook_keeps_default_for_unauthenticated_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_UPSTREAM_BEARER_CONNECTORS", raising=False)
    hook = relay_auth_provider_hook()
    assert hook("demo", {"provider": "none"}) is None
    assert hook("demo", {}) is None
    with pytest.raises(ValueError, match="NW_UPSTREAM_BEARER_CONNECTORS"):
        hook("demo", {"provider": "static_token", "secret_key": "X"})
