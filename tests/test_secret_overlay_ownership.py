#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The secret overlay is shared by tenant secrets and MCP OAuth tokens: each writer only
replaces the keys it owns, and an upserted tenant secret is durable without a later save."""

from __future__ import annotations

import threading

import pytest

from bindings.factory import ConnectorFactory
from node_wire_runtime.secrets import OverlaySecretProvider, tenant_scoped_secret_key
from node_wire_runtime.tenant_persistence import (
    clear_config_secrets,
    list_secret_logical_keys,
    load_tenants,
    save_tenants,
    tenant_secret_store,
    upsert_tenant_secrets,
)

_TOKEN = "ya29.test-token"  # nosec B105 - test fixture


def _drive_secret(config_name: str = "test") -> str:
    return tenant_scoped_secret_key(
        "acme", "google_drive", "GOOGLE_DRIVE_ACCESS_TOKEN", config_name=config_name
    )


def test_replace_all_only_replaces_its_own_namespace() -> None:
    overlay = OverlaySecretProvider()
    overlay.set_secret("NW_MCP_OAUTH_TOKEN_X", "token")
    overlay.replace_all({"A": "1", "B": "2"}, namespace="tenants")
    overlay.replace_all({"B": "3"}, namespace="tenants")
    assert overlay.export() == {"NW_MCP_OAUTH_TOKEN_X": "token", "B": "3"}


def test_loading_tenants_keeps_mcp_oauth_tokens() -> None:
    store = ConnectorFactory().store
    upsert_tenant_secrets(
        "acme", "google_drive", {"GOOGLE_DRIVE_ACCESS_TOKEN": _TOKEN}, config_name="test"
    )
    store.create(
        "acme",
        "google_drive",
        {"name": "test", "default": True, "auth": {"provider": "service_account"}},
    )
    save_tenants(store)
    overlay = OverlaySecretProvider.instance()
    overlay.set_secret("NW_MCP_OAUTH_TOKEN_USER", '{"access_token": "t"}')

    load_tenants(ConnectorFactory().store)

    assert overlay.get_secret("NW_MCP_OAUTH_TOKEN_USER") == '{"access_token": "t"}'
    assert overlay.get_secret(_drive_secret()) == _TOKEN


def test_clearing_config_secrets_keeps_other_keys() -> None:
    upsert_tenant_secrets(
        "acme", "google_drive", {"GOOGLE_DRIVE_ACCESS_TOKEN": _TOKEN}, config_name="test"
    )
    overlay = OverlaySecretProvider.instance()
    overlay.set_secret("NW_MCP_OAUTH_TOKEN_USER", "t")
    clear_config_secrets("acme", "google_drive", "test")
    assert overlay.export() == {"NW_MCP_OAUTH_TOKEN_USER": "t"}


def test_upsert_is_durable_without_save() -> None:
    upsert_tenant_secrets(
        "acme", "google_drive", {"GOOGLE_DRIVE_ACCESS_TOKEN": _TOKEN}, config_name="test"
    )
    store = tenant_secret_store()
    assert store is not None
    assert store.load()["acme"]["google_drive"]["test"] == {"GOOGLE_DRIVE_ACCESS_TOKEN": _TOKEN}


def test_failed_store_write_leaves_overlay_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        def load(self):  # pragma: no cover - not reached
            return {}

        def save(self, mirror):
            raise OSError("disk full")

    monkeypatch.setattr(
        "node_wire_runtime.tenant_persistence.tenant_secret_store", lambda: _Broken()
    )
    with pytest.raises(OSError):
        upsert_tenant_secrets(
            "acme", "google_drive", {"GOOGLE_DRIVE_ACCESS_TOKEN": _TOKEN}, config_name="test"
        )
    assert OverlaySecretProvider.instance().export() == {}
    assert list_secret_logical_keys("acme", "google_drive", "test") == []


def test_concurrent_writers_do_not_lose_keys() -> None:
    overlay = OverlaySecretProvider()
    errors: list[BaseException] = []

    def tenants() -> None:
        try:
            for i in range(300):
                overlay.replace_all({f"T{i % 7}": str(i)}, namespace="tenants")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def tokens() -> None:
        for i in range(300):
            overlay.set_secret(f"NW_MCP_OAUTH_TOKEN_{i}", "t")

    threads = [threading.Thread(target=tenants), threading.Thread(target=tokens)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sum(1 for k in overlay.export() if k.startswith("NW_MCP_OAUTH_TOKEN_")) == 300
