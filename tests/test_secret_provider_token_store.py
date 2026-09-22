#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""SecretProviderTokenStore must persist via OverlaySecretProvider, not os.environ."""

from __future__ import annotations

import pytest

from node_wire_runtime.mcp_client.config import (
    AuthConfig,
    AuthTokenConfig,
    McpClientConfig,
    McpServerConfig,
    TokenStoreMode,
)
from node_wire_runtime.mcp_client.token_storage import (
    SecretProviderTokenStore,
    make_token_store,
    stored_from_oauth_response,
    token_partition_key,
)
from node_wire_runtime.secrets import OverlaySecretProvider


@pytest.fixture(autouse=True)
def _clean_overlay():
    OverlaySecretProvider.instance().clear()
    yield
    OverlaySecretProvider.instance().clear()


def _tokens():
    return stored_from_oauth_response(
        user_id="user-1",
        mcp_server_url="https://mcp.example.com/mcp",
        issuer="https://issuer.example",
        access_token="access-abc",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="refresh-xyz",
        scope="read write",
    )


def test_save_writes_through_overlay_not_os_environ() -> None:
    """The bug this fixes: save() used to write directly to os.environ, bypassing the
    injected SecretProvider abstraction entirely. It must go through the overlay."""
    import os

    store = SecretProviderTokenStore(OverlaySecretProvider.instance())
    tokens = _tokens()
    before = dict(os.environ)
    store.save(tokens)
    assert os.environ == before, "save() must not touch os.environ directly"

    partition_key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
    assert store.get(partition_key) == tokens


def test_save_then_get_round_trips() -> None:
    store = SecretProviderTokenStore(OverlaySecretProvider.instance())
    tokens = _tokens()
    store.save(tokens)

    fetched = store.get(token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer))
    assert fetched == tokens


def test_delete_removes_the_entry() -> None:
    store = SecretProviderTokenStore(OverlaySecretProvider.instance())
    tokens = _tokens()
    partition_key = token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer)
    store.save(tokens)
    assert store.get(partition_key) is not None

    store.delete(partition_key)
    assert store.get(partition_key) is None


def test_delete_is_a_noop_when_nothing_was_saved() -> None:
    store = SecretProviderTokenStore(OverlaySecretProvider.instance())
    store.delete("never-saved-partition-key")  # must not raise


def test_make_token_store_default_provider_sees_its_own_writes() -> None:
    """The gap this closes: make_token_store's default reader was a bare
    EnvSecretProvider(), which would never see a save() written to the overlay. The
    default must chain the overlay in front so save() -> get() round-trips out of the
    box, with no explicit secret_provider injection required."""
    config = McpClientConfig(
        server=McpServerConfig(url="https://mcp.example.com/mcp"),
        auth=AuthConfig(token=AuthTokenConfig(store=TokenStoreMode.CONFIGURED_SECRET_STORE)),
    )
    store = make_token_store(config)
    assert isinstance(store, SecretProviderTokenStore)

    tokens = _tokens()
    store.save(tokens)
    fetched = store.get(token_partition_key(tokens.user_id, tokens.mcp_server_url, tokens.issuer))
    assert fetched == tokens


def test_overlay_unset_removes_key_and_is_idempotent() -> None:
    overlay = OverlaySecretProvider.instance()
    overlay.set_secret("SOME_KEY", "value")
    overlay.unset("SOME_KEY")
    with pytest.raises(Exception):
        overlay.get_secret("SOME_KEY")
    overlay.unset("SOME_KEY")  # must not raise on a key that's already gone
