#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Header-based tenancy and runtime config API (config store, factory, identity)."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from bindings.factory import ConnectorFactory
from bindings.rest_api.app import app, get_factory
from node_wire_runtime import BaseConnector, sdk_action
from node_wire_runtime.config_store import (
    ConfigNameConflictError,
    ConfigNotFoundError,
    ConnectorConfigStore,
    DefaultDeletionError,
    redact,
)
from node_wire_runtime.identity import resolve_tenant_id, tenant_from_headers
from node_wire_runtime.models import ConnectorResponse
from node_wire_runtime.secrets import (
    EnvSecretProvider,
    TenantSecretNotFoundError,
    TenantSecretProvider,
)
import node_wire_runtime.identity as identity_mod


# --------------------------------------------------------------------------- #
# Config store lifecycle
# --------------------------------------------------------------------------- #


def _doc(name: str, default: bool | None = None, **config):
    d: dict = {"name": name, "config": config}
    if default is not None:
        d["default"] = default
    return d


def test_init_first_is_default_when_none_marked():
    store = ConnectorConfigStore()
    store.init({"acme": {"slack": [_doc("a"), _doc("b")]}})
    assert store.resolve("acme", "slack", None).name == "a"


def test_init_honours_explicit_default():
    store = ConnectorConfigStore()
    store.init({"acme": {"slack": [_doc("a"), _doc("b", default=True)]}})
    assert store.resolve("acme", "slack", None).name == "b"


def test_init_rejects_two_defaults():
    store = ConnectorConfigStore()
    with pytest.raises(Exception):
        store.init({"acme": {"slack": [_doc("a", default=True), _doc("b", default=True)]}})


def test_create_first_config_auto_defaults():
    store = ConnectorConfigStore()
    rec = store.create("acme", "slack", _doc("only"))
    assert rec.default is True
    assert store.resolve("acme", "slack", None).name == "only"


def test_create_duplicate_name_conflicts():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    with pytest.raises(ConfigNameConflictError):
        store.create("acme", "slack", _doc("a"))


def test_delete_default_with_siblings_requires_new_default():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    store.create("acme", "slack", _doc("b"))
    with pytest.raises(DefaultDeletionError):
        store.delete("acme", "slack", "a")  # 'a' is default, 'b' remains


def test_delete_default_moves_flag_with_new_default():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    store.create("acme", "slack", _doc("b"))
    store.delete("acme", "slack", "a", new_default="b")
    assert store.resolve("acme", "slack", None).name == "b"


def test_delete_last_config_removes_scope():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    store.delete("acme", "slack", "a")  # last config: allowed without new_default
    assert store.has_config("acme", "slack") is False
    with pytest.raises(ConfigNotFoundError):
        store.resolve("acme", "slack", None)


def test_update_name_is_immutable():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    with pytest.raises(Exception):
        store.update("acme", "slack", "a", _doc("renamed"))


def test_set_default_moves_exactly_one_flag():
    store = ConnectorConfigStore()
    store.create("acme", "slack", _doc("a"))
    store.create("acme", "slack", _doc("b"))
    store.set_default("acme", "slack", "b")
    docs = {d["name"]: d["default"] for d in store.list("acme", "slack")}
    assert docs == {"a": False, "b": True}


def test_redaction_masks_inline_values_on_read():
    store = ConnectorConfigStore()
    store.create(
        "acme",
        "slack",
        {
            "name": "a",
            "auth": {"provider": "static_token", "token_value": "xoxb-secret"},
        },
    )
    got = store.get("acme", "slack", "a")
    assert got["auth"]["token_value"] != "xoxb-secret"
    # The internal resolve path still sees the real value.
    assert store.resolve("acme", "slack", "a").raw["auth"]["token_value"] == "xoxb-secret"


def test_redact_passes_references_through():
    out = redact({"auth": {"secret_key": "announcement_token", "password": "p"}})
    assert out["auth"]["secret_key"] == "announcement_token"
    assert out["auth"]["password"] != "p"


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def test_tenant_from_headers_case_insensitive():
    assert tenant_from_headers({"X-Tenant-ID": "acme"}) == "acme"
    assert tenant_from_headers({"x-tenant-id": "acme"}) == "acme"


def test_missing_header_resolves_default():
    assert resolve_tenant_id(headers={}) == "__default__"
    assert resolve_tenant_id(headers={"X-Tenant-ID": "  "}) == "__default__"


def test_header_override_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(identity_mod, "TENANT_HEADER", "x-org-id")
    assert tenant_from_headers({"X-Org-ID": "globex"}) == "globex"


def test_jwt_fallback_when_no_header():
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert resolve_tenant_id(headers={}, jwt_identity=ident) == "t-1"


def test_header_wins_over_jwt():
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert resolve_tenant_id(headers={"X-Tenant-ID": "acme"}, jwt_identity=ident) == "acme"


def test_env_pin_wins_over_everything():
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert (
        resolve_tenant_id(headers={"X-Tenant-ID": "acme"}, jwt_identity=ident, env_pin="stdio-tenant")
        == "stdio-tenant"
    )


# --------------------------------------------------------------------------- #
# TenantSecretProvider
# --------------------------------------------------------------------------- #


def test_tenant_secret_provider_env_translation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_ACME_SLACK_ANNOUNCEMENT_TOKEN", "xoxb-1")
    provider = TenantSecretProvider(EnvSecretProvider(), "acme", "slack")
    assert provider.get_secret("announcement_token") == "xoxb-1"


def test_tenant_secret_provider_is_strict(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NW_ACME_SLACK_MISSING", raising=False)
    provider = TenantSecretProvider(EnvSecretProvider(), "acme", "slack")
    with pytest.raises(TenantSecretNotFoundError):
        provider.get_secret("missing")


# --------------------------------------------------------------------------- #
# Factory: three-part key, default routing, invalidation
# --------------------------------------------------------------------------- #


def _bare_factory(monkeypatch: pytest.MonkeyPatch) -> ConnectorFactory:
    """Factory whose instantiation returns a fresh stub per call (no real connector)."""
    factory = ConnectorFactory()
    monkeypatch.setattr(
        factory, "_instantiate", lambda record: MagicMock(spec=BaseConnector)
    )
    return factory


async def test_factory_three_part_key_isolates_instances(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init(
        {
            "acme": {"slack": [_doc("internal", default=True), _doc("announce")]},
            "globex": {"slack": [_doc("main")]},
        }
    )
    a1 = await factory.get("slack", tenant_id="acme", config_name="internal")
    a2 = await factory.get("slack", tenant_id="acme", config_name="announce")
    g1 = await factory.get("slack", tenant_id="globex", config_name="main")
    assert a1 is not a2  # two configs of one connector -> distinct instances
    assert a1 is not g1  # two tenants -> distinct instances


async def test_default_and_explicit_share_one_instance(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init({"acme": {"slack": [_doc("internal", default=True), _doc("announce")]}})
    via_default = await factory.get("slack", tenant_id="acme", config_name=None)
    via_name = await factory.get("slack", tenant_id="acme", config_name="internal")
    assert via_default is via_name


async def test_moving_default_reroutes_without_duplicating(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init({"acme": {"slack": [_doc("internal", default=True), _doc("announce")]}})
    first_default = await factory.get("slack", tenant_id="acme")
    factory.store.set_default("acme", "slack", "announce")
    new_default = await factory.get("slack", tenant_id="acme")
    explicit_announce = await factory.get("slack", tenant_id="acme", config_name="announce")
    assert new_default is not first_default
    assert new_default is explicit_announce


async def test_write_invalidates_cached_instance(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init({"acme": {"slack": [_doc("internal", default=True)]}})
    before = await factory.get("slack", tenant_id="acme", config_name="internal")
    factory.store.update("acme", "slack", "internal", _doc("internal", channel="#new"))
    after = await factory.get("slack", tenant_id="acme", config_name="internal")
    assert before is not after  # write evicted the cached instance


async def test_off_loop_write_does_not_raise(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init({"acme": {"slack": [_doc("a", default=True)]}})
    await factory.get("slack", tenant_id="acme", config_name="a")

    errors: list[Exception] = []

    def _mutate() -> None:
        try:
            factory.store.delete("acme", "slack", "a")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=_mutate)
    t.start()
    t.join()
    assert errors == []
    assert factory.store.has_config("acme", "slack") is False


# --------------------------------------------------------------------------- #
# Fail-closed
# --------------------------------------------------------------------------- #


async def test_unconfigured_and_unknown_name_both_raise(monkeypatch: pytest.MonkeyPatch):
    factory = _bare_factory(monkeypatch)
    factory.store.init({"acme": {"slack": [_doc("a", default=True)]}})
    with pytest.raises(ConfigNotFoundError):
        await factory.get("slack", tenant_id="acme", config_name="does-not-exist")
    with pytest.raises(ConfigNotFoundError):
        await factory.get("slack", tenant_id="nobody")


def test_rest_fail_closed_returns_indistinguishable_403():
    mock_factory = MagicMock()
    mock_factory.is_exposed.return_value = True
    mock_factory.get = AsyncMock(side_effect=ConfigNotFoundError("secret internals"))
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        unknown_scope = client.post("/connectors/http_generic/request", json={})
        unknown_name = client.post(
            "/connectors/http_generic/request", json={"config_name": "nope"}
        )
    finally:
        app.dependency_overrides.clear()
    assert unknown_scope.status_code == 403
    assert unknown_name.status_code == 403
    # Same body: the internal reason is never leaked, so names cannot be enumerated.
    assert unknown_scope.json() == unknown_name.json()
    assert "secret internals" not in unknown_scope.text


# --------------------------------------------------------------------------- #
# Direct integration (no bindings) — the library-framing acceptance test
# --------------------------------------------------------------------------- #


class _EchoIn(BaseModel):
    action: str = "echo"
    text: str = ""


class _EchoOut(BaseModel):
    text: str = ""
    channel: str = ""


class _EchoConnector(BaseConnector):
    connector_id = "test_echo"
    output_model = _EchoOut

    @sdk_action("echo", requires_auth=False)
    async def echo(self, params: _EchoIn, *, trace_id: str) -> _EchoOut:
        return _EchoOut(text=params.text, channel=self.config.get("channel", ""))


async def test_direct_integration_store_factory_run(monkeypatch: pytest.MonkeyPatch):
    factory = ConnectorFactory()
    factory.store.init(
        {
            "acme": {
                "test_echo": [
                    {"name": "primary", "default": True, "config": {"channel": "#eng"}}
                ]
            }
        }
    )
    connector = await factory.get("test_echo", tenant_id="acme")
    assert connector._config_name == "primary"
    assert connector.config == {"channel": "#eng"}

    resp: ConnectorResponse = await connector.run(
        {"action": "echo", "text": "hi"}, tenant_id="acme"
    )
    assert resp.success is True
    assert resp.data["text"] == "hi"
    assert resp.data["channel"] == "#eng"  # per-config injection reached the connector
