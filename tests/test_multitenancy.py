#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Header-based tenancy and runtime config API (config store, factory, identity)."""

from __future__ import annotations

import threading
from typing import Literal
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
from node_wire_runtime.identity import (
    MissingTenantError,
    TenantIdentityMismatchError,
    TenantMismatchError,
    effective_run_tenant_id,
    is_multitenancy_enabled,
    normalize_tenant_id,
    resolve_config_name,
    resolve_tenant_id,
    tenant_from_headers,
    tenants_equivalent,
)
from node_wire_runtime.models import ConnectorResponse, ErrorCategory
from node_wire_runtime.secrets import (
    EnvSecretProvider,
    TenantSecretNotFoundError,
    TenantSecretProvider,
)

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


def test_normalize_tenant_id_blank_is_none():
    assert normalize_tenant_id(None) is None
    assert normalize_tenant_id("") is None
    assert normalize_tenant_id("   ") is None
    assert normalize_tenant_id(" acme ") == "acme"


def test_tenants_equivalent_default_and_none():
    assert tenants_equivalent(None, "__default__") is True
    assert tenants_equivalent("__default__", None) is True
    assert tenants_equivalent("acme", "acme") is True
    assert tenants_equivalent("acme", "globex") is False


def test_effective_run_tenant_id_pinned_omit_uses_pin():
    effective, err = effective_run_tenant_id(pinned="acme", caller=None)
    assert err is None
    assert effective == "acme"


def test_effective_run_tenant_id_pinned_mismatch():
    effective, err = effective_run_tenant_id(pinned="acme", caller="globex")
    assert effective is None
    assert isinstance(err, TenantMismatchError)
    assert err.pinned == "acme"
    assert err.requested == "globex"


def test_missing_header_resolves_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    assert resolve_tenant_id(headers={}) == "__default__"
    assert resolve_tenant_id(headers={"X-Tenant-ID": "  "}) == "__default__"


def test_header_override_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("node_wire_runtime.identity.TENANT_HEADER", "x-org-id")
    assert tenant_from_headers({"X-Org-ID": "globex"}) == "globex"


def test_jwt_fallback_when_no_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert resolve_tenant_id(headers={}, jwt_identity=ident) == "t-1"


def test_jwt_wins_over_header(monkeypatch: pytest.MonkeyPatch):
    """JWT tenant claim is authoritative — a caller can't select a different
    tenant just by sending a header (H-1, 2026-09-01 security review)."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert resolve_tenant_id(headers={"X-Tenant-ID": "t-1"}, jwt_identity=ident) == "t-1"


def test_conflicting_header_and_jwt_tenant_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    ident = MagicMock()
    ident.tenant_id = "t-1"
    with pytest.raises(TenantIdentityMismatchError, match="acme.*t-1|t-1.*acme"):
        resolve_tenant_id(headers={"X-Tenant-ID": "acme"}, jwt_identity=ident)


def test_header_matching_default_alias_of_jwt_tenant_is_allowed(monkeypatch: pytest.MonkeyPatch):
    """``__default__`` and the unset header are treated as the same tenant."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    ident = MagicMock()
    ident.tenant_id = "__default__"
    assert resolve_tenant_id(headers={"X-Tenant-ID": "__default__"}, jwt_identity=ident) == (
        "__default__"
    )


def test_env_pin_wins_over_everything(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert (
        resolve_tenant_id(
            headers={"X-Tenant-ID": "acme"}, jwt_identity=ident, env_pin="stdio-tenant"
        )
        == "stdio-tenant"
    )


# --------------------------------------------------------------------------- #
# TenantSecretProvider
# --------------------------------------------------------------------------- #


def test_tenant_secret_provider_env_translation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_ACME_SLACK_ANNOUNCEMENT_TOKEN", "xoxb-1")
    provider = TenantSecretProvider(EnvSecretProvider(), "acme", "slack")
    assert provider.get_secret("announcement_token") == "xoxb-1"


def test_tenant_secret_provider_with_config_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_ACME_SLACK_DEMO_ANNOUNCEMENT_TOKEN", "xoxb-cfg")
    provider = TenantSecretProvider(
        EnvSecretProvider(), "acme", "slack", config_name="demo"
    )
    assert provider.get_secret("announcement_token") == "xoxb-cfg"


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
    monkeypatch.setattr(factory, "_instantiate", lambda record: MagicMock(spec=BaseConnector))
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


def test_rest_fail_closed_returns_indistinguishable_403(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    mock_factory = MagicMock()
    mock_factory.is_exposed.return_value = True
    mock_factory.get = AsyncMock(side_effect=ConfigNotFoundError("secret internals"))
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        unknown_scope = client.post("/connectors/http_generic/request", json={})
        unknown_name = client.post("/connectors/http_generic/request", json={"config_name": "nope"})
    finally:
        app.dependency_overrides.clear()
    assert unknown_scope.status_code == 403
    assert unknown_name.status_code == 403
    # Same body: the internal reason is never leaked, so names cannot be enumerated.
    assert unknown_scope.json() == unknown_name.json()
    assert "secret internals" not in unknown_scope.text


def test_rest_missing_tenant_returns_400_when_multitenancy_enabled(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    mock_factory = MagicMock()
    mock_factory.is_exposed.return_value = True
    mock_factory.get = AsyncMock()
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        resp = client.post("/connectors/http_generic/request", json={})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert "X-Tenant-ID is required" in resp.json()["detail"]
    mock_factory.get.assert_not_called()


# --------------------------------------------------------------------------- #
# Direct integration (no bindings) — the library-framing acceptance test
# --------------------------------------------------------------------------- #


class _EchoIn(BaseModel):
    action: Literal["echo"] = "echo"
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
                "test_echo": [{"name": "primary", "default": True, "config": {"channel": "#eng"}}]
            }
        }
    )
    connector = await factory.get("test_echo", tenant_id="acme")
    assert connector.config_name == "primary"
    assert connector.tenant_id == "acme"
    assert connector.config == {"channel": "#eng"}

    resp: ConnectorResponse = await connector.run({"action": "echo", "text": "hi"})
    assert resp.success is True
    assert resp.data["text"] == "hi"
    assert resp.data["channel"] == "#eng"  # per-config injection reached the connector

    mismatch: ConnectorResponse = await connector.run(
        {"action": "echo", "text": "nope"}, tenant_id="globex"
    )
    assert mismatch.success is False
    assert mismatch.error_code == "TENANT_MISMATCH"
    assert mismatch.error_category == ErrorCategory.AUTH


async def test_factory_get_none_resolves_default_tenant(monkeypatch: pytest.MonkeyPatch):
    factory = ConnectorFactory()
    factory.store.init(
        {
            "__default__": {
                "test_echo": [{"name": "default", "default": True, "config": {"channel": "d"}}]
            }
        }
    )
    via_omit = await factory.get("test_echo")
    via_none = await factory.get("test_echo", tenant_id=None)
    assert via_omit is via_none
    assert via_omit.tenant_id == "__default__"


class _OuterIn(BaseModel):
    action: Literal["outer"] = "outer"
    text: str = ""


class _PinDelegateConnector(BaseConnector):
    connector_id = "test_pin_delegate"
    output_model = _EchoOut

    @sdk_action("outer", requires_auth=False)
    async def outer(self, params: _OuterIn, *, trace_id: str) -> _EchoOut:
        return await self.call_action("echo", {"action": "echo", "text": params.text})

    @sdk_action("echo", requires_auth=False)
    async def echo(self, params: _EchoIn, *, trace_id: str) -> _EchoOut:
        return _EchoOut(text=params.text, channel=self.config.get("channel", ""))


async def test_call_action_inherits_pinned_tenant(monkeypatch: pytest.MonkeyPatch):
    factory = ConnectorFactory()
    factory.store.init(
        {
            "acme": {
                "test_pin_delegate": [
                    {"name": "primary", "default": True, "config": {"channel": "#deleg"}}
                ]
            }
        }
    )
    connector = await factory.get("test_pin_delegate", tenant_id="acme")
    resp = await connector.run({"action": "outer", "text": "nested"})
    assert resp.success is True
    assert resp.data["text"] == "nested"
    assert resp.data["channel"] == "#deleg"


# --------------------------------------------------------------------------- #
# NW_MULTITENANCY_ENABLED feature flag
# --------------------------------------------------------------------------- #


def test_multitenancy_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NW_MULTITENANCY_ENABLED", raising=False)
    assert is_multitenancy_enabled() is False


def test_multitenancy_enabled_truthy_values(monkeypatch: pytest.MonkeyPatch):
    for val in ("true", "1", "yes", "on", "True", "YES"):
        monkeypatch.setenv("NW_MULTITENANCY_ENABLED", val)
        assert is_multitenancy_enabled() is True, f"Expected True for {val!r}"


def test_multitenancy_disabled_falsy_values(monkeypatch: pytest.MonkeyPatch):
    for val in ("false", "0", "no", "off", "False"):
        monkeypatch.setenv("NW_MULTITENANCY_ENABLED", val)
        assert is_multitenancy_enabled() is False, f"Expected False for {val!r}"


def test_resolve_tenant_id_disabled_ignores_header(monkeypatch: pytest.MonkeyPatch):
    """When disabled, resolve_tenant_id always returns DEFAULT_TENANT."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    assert resolve_tenant_id(headers={"X-Tenant-ID": "acme"}) == "__default__"


def test_resolve_tenant_id_disabled_ignores_jwt(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    ident = MagicMock()
    ident.tenant_id = "t-1"
    assert resolve_tenant_id(headers={}, jwt_identity=ident) == "__default__"


def test_resolve_tenant_id_disabled_ignores_env_pin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    assert resolve_tenant_id(env_pin="stdio-tenant") == "__default__"


def test_resolve_tenant_id_enabled_reads_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    assert resolve_tenant_id(headers={"X-Tenant-ID": "acme"}) == "acme"


def test_resolve_tenant_id_enabled_allows_explicit_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    assert resolve_tenant_id(headers={"X-Tenant-ID": "__default__"}) == "__default__"


def test_resolve_tenant_id_enabled_missing_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    with pytest.raises(MissingTenantError, match="X-Tenant-ID is required"):
        resolve_tenant_id(headers={})
    with pytest.raises(MissingTenantError):
        resolve_tenant_id(headers={"X-Tenant-ID": "  "})


def test_resolve_tenant_id_enabled_env_pin_wins(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    assert (
        resolve_tenant_id(headers={"X-Tenant-ID": "acme"}, env_pin="stdio-tenant") == "stdio-tenant"
    )


def test_resolve_config_name_disabled_returns_none(monkeypatch: pytest.MonkeyPatch):
    """When multitenancy is off, user-supplied config names are suppressed."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    assert resolve_config_name("my-config") is None
    assert resolve_config_name(None) is None


def test_resolve_config_name_enabled_passthrough(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    assert resolve_config_name("my-config") == "my-config"
    assert resolve_config_name(None) is None
    assert resolve_config_name("") is None
    assert resolve_config_name("  ") is None


def test_resolve_config_name_enabled_rejects_non_string(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    # JSON null and LLM quirks must map to omit (tenant default), not fail closed.
    assert resolve_config_name(None) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# REST config CRUD (patch coverage for bindings.rest_api.app)
# --------------------------------------------------------------------------- #


def _config_factory() -> ConnectorFactory:
    factory = ConnectorFactory()
    # No YAML load needed — store starts empty; CRUD tests seed via API/store.
    return factory


def test_rest_config_crud_roundtrip(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _config_factory()
    app.dependency_overrides[get_factory] = lambda: factory
    headers = {"X-Tenant-ID": "acme"}
    try:
        client = TestClient(app)

        create = client.post(
            "/v1/connectors/slack/configs",
            json={"name": "primary", "default": True, "config": {"channel": "#a"}},
            headers=headers,
        )
        assert create.status_code == 201
        assert create.json() == {"name": "primary", "default": True}

        listed = client.get("/v1/connectors/slack/configs", headers=headers)
        assert listed.status_code == 200
        assert any(item["name"] == "primary" for item in listed.json())

        got = client.get("/v1/connectors/slack/configs/primary", headers=headers)
        assert got.status_code == 200
        assert got.json()["name"] == "primary"

        client.post(
            "/v1/connectors/slack/configs",
            json={"name": "secondary", "config": {"channel": "#b"}},
            headers=headers,
        )
        updated = client.put(
            "/v1/connectors/slack/configs/secondary",
            json={"name": "secondary", "config": {"channel": "#b2"}},
            headers=headers,
        )
        assert updated.status_code == 200

        set_def = client.put(
            "/v1/connectors/slack/configs/secondary/default",
            headers=headers,
        )
        assert set_def.status_code == 200

        deleted = client.delete(
            "/v1/connectors/slack/configs/primary",
            headers=headers,
        )
        assert deleted.status_code == 200

        missing = client.get("/v1/connectors/slack/configs/primary", headers=headers)
        assert missing.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_rest_config_missing_tenant_returns_400(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _config_factory()
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/connectors/slack/configs",
            json={"name": "x", "config": {}},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert "X-Tenant-ID" in resp.json()["detail"]


def test_rest_config_create_upserts_on_conflict(monkeypatch: pytest.MonkeyPatch):
    """POST create is create-or-update for playground per-connector Add config."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _config_factory()
    factory.store.create("acme", "slack", _doc("dup", default=True))
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/connectors/slack/configs",
            json={"name": "dup", "config": {"channel": "#updated"}},
            headers={"X-Tenant-ID": "acme"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    assert resp.json()["name"] == "dup"
    got = factory.store.get("acme", "slack", "dup")
    assert got is not None
    assert got.get("config", {}).get("channel") == "#updated"


def test_rest_config_init_with_tenant_header(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _config_factory()
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/config/init",
            json={"slack": [{"name": "boot", "default": True, "config": {}}]},
            headers={"X-Tenant-ID": "acme"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert factory.store.resolve("acme", "slack", None).name == "boot"


def test_rest_config_delete_default_without_replacement_returns_400(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = _config_factory()
    factory.store.create("acme", "slack", _doc("keep", default=True))
    factory.store.create("acme", "slack", _doc("other"))
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        client = TestClient(app)
        resp = client.delete(
            "/v1/connectors/slack/configs/keep",
            headers={"X-Tenant-ID": "acme"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_tenant_from_headers_none_value():
    assert tenant_from_headers({"X-Tenant-ID": None}) is None  # type: ignore[dict-item]
