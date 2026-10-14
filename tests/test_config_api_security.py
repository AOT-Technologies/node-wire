#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Runtime config documents cannot name process env vars or retarget a connector host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel

from bindings.factory import ConnectorFactory
from bindings.invoke import invoke
from bindings.rest_api.app import app, get_factory
from node_wire_runtime import nw_action
from node_wire_runtime.config_store import (
    DEFAULT_TENANT,
    ConfigNotFoundError,
    ConnectorConfigStore,
)
from node_wire_runtime.http_safety import assert_base_url_allowed
from node_wire_runtime.rest import RestConnector, RestResponseOutput
from node_wire_runtime.secrets import tenant_scoped_secret_key
from node_wire_runtime.tenant_persistence import (
    assert_secret_ref,
    declare_secret_shape,
    load_tenants,
)

_CONNECTOR = "sec_demo_rest"
_LOGICAL = "DEMO_REST_TOKEN"
_JWT = "signing-secret-value"
_API_KEY = "unit-test-key"
_SCOPE = "mcp:sec_demo_rest.ping"


class _PingInput(BaseModel):
    action: Literal["ping"] = "ping"


class SecRestConnector(RestConnector):
    connector_id = _CONNECTOR
    default_base_url = "https://api.example.com"
    output_model = RestResponseOutput

    @nw_action("ping")
    async def ping(self, params: _PingInput, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET",
            "/ping",
            params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
        )


def _declare() -> None:
    declare_secret_shape(_CONNECTOR, formats={_LOGICAL: "opaque_secret"})


def _auth_env(monkeypatch: pytest.MonkeyPatch, *, multitenancy: bool) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_REST_API_KEY", _API_KEY)
    monkeypatch.setenv("NW_REST_JWT_SECRET", _JWT)
    monkeypatch.setenv("NW_REST_API_KEY_SCOPES", _SCOPE)
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "deny")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true" if multitenancy else "false")
    monkeypatch.setenv(_LOGICAL, "benign-token")


def _client(factory: ConnectorFactory) -> TestClient:
    app.dependency_overrides[get_factory] = lambda: factory
    return TestClient(app)


def _headers(**extra: str) -> dict[str, str]:
    headers = {"X-API-Key": _API_KEY}
    headers.update(extra)
    return headers


def _install_http(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self) -> dict:
            return {"ok": True}

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _FakeResponse:
            captured.append(kwargs)
            return _FakeResponse()

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr("node_wire_runtime.rest.assert_safe_destination", _allow)
    return captured


def _doc(secret_key: str, *, base_url: str | None = None, auth: dict | None = None) -> dict:
    body: dict[str, Any] = {
        "name": "live",
        "default": True,
        "auth": auth or {"provider": "static_token", "secret_key": secret_key, "prefix": "Bearer"},
    }
    if base_url is not None:
        body["base_url"] = base_url
    return body


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> ConnectorFactory:
    _declare()
    _auth_env(monkeypatch, multitenancy=True)
    built = ConnectorFactory()
    yield built
    app.dependency_overrides.clear()


def test_create_rejects_raw_env_and_other_tenant_secret_refs(
    factory: ConnectorFactory,
) -> None:
    client = _client(factory)
    headers = _headers(**{"X-Tenant-ID": "acme"})
    cases = [
        _doc("NW_REST_JWT_SECRET", base_url="https://evil.example"),
        _doc("NW_ACME_SLACK_LIVE_SLACK_BOT_TOKEN", base_url="https://evil.example"),
        _doc(
            _LOGICAL,
            auth={
                "provider": "oauth2",
                "grant_method": "client_secret_post",
                "token_url_secret": "NW_REST_JWT_SECRET",
                "client_id_secret": _LOGICAL,
                "client_secret_secret": "NW_REST_JWT_SECRET",
            },
            base_url="https://api.example.com",
        ),
    ]
    for body in cases:
        resp = client.post(f"/v1/connectors/{_CONNECTOR}/configs", json=body, headers=headers)
        assert resp.status_code == 400, resp.text
        assert _JWT not in resp.text
        assert "NW_REST_JWT_SECRET" not in resp.text


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://evil.example\\@api.example.com",
        "https://u:p@api.example.com",
    ],
)
def test_base_url_downgrade_userinfo_and_backslash_refused(url: str) -> None:
    with pytest.raises(ValueError, match="base_url host is not allowed"):
        assert_base_url_allowed(url, baked_base_url="https://api.example.com")


def test_base_url_allowlist_at_write_and_call(
    factory: ConnectorFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _install_http(monkeypatch)
    client = _client(factory)
    headers = _headers(**{"X-Tenant-ID": "acme"})
    monkeypatch.delenv("NW_REST_ALLOWED_HOSTS", raising=False)
    rejected = client.post(
        f"/v1/connectors/{_CONNECTOR}/configs",
        json=_doc(_LOGICAL, base_url="https://evil.example"),
        headers=headers,
    )
    assert rejected.status_code == 400, rejected.text
    assert captured == []

    monkeypatch.setenv("NW_REST_ALLOWED_HOSTS", "evil.example")
    scoped = tenant_scoped_secret_key("acme", _CONNECTOR, _LOGICAL, config_name="live")
    monkeypatch.setenv(scoped, "benign-token")
    created = client.post(
        f"/v1/connectors/{_CONNECTOR}/configs",
        json=_doc(_LOGICAL, base_url="https://evil.example/v1"),
        headers=headers,
    )
    assert created.status_code == 201, created.text

    import asyncio

    response = asyncio.run(
        invoke(
            factory,
            connector_id=_CONNECTOR,
            action="ping",
            payload={},
            protocol="rest",
            tenant_id="acme",
            principal="api-key-user",
            scopes=(_SCOPE,),
        )
    )
    assert response.success, response.message
    assert captured
    blob = json.dumps(captured)
    assert _JWT not in blob
    assert "Bearer benign-token" in blob


def test_call_time_rejects_tampered_base_url_and_secret_key(
    factory: ConnectorFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _install_http(monkeypatch)
    client = _client(factory)
    headers = _headers(**{"X-Tenant-ID": "acme"})
    created = client.post(
        f"/v1/connectors/{_CONNECTOR}/configs",
        json=_doc(_LOGICAL, base_url="https://api.example.com"),
        headers=headers,
    )
    assert created.status_code == 201, created.text

    record = factory.store.resolve("acme", _CONNECTOR, "live")
    record.raw["base_url"] = "https://evil.example"
    record.raw["auth"]["secret_key"] = "NW_REST_JWT_SECRET"
    factory.invalidate_configs("acme", _CONNECTOR, ["live"])

    import asyncio

    response = asyncio.run(
        invoke(
            factory,
            connector_id=_CONNECTOR,
            action="ping",
            payload={},
            protocol="rest",
            tenant_id="acme",
            principal="api-key-user",
            scopes=(_SCOPE,),
        )
    )
    assert not response.success
    assert captured == []
    assert _JWT not in (response.message or "")


@pytest.mark.parametrize("multitenancy", [False, True])
def test_default_tenant_config_writes_rejected(
    monkeypatch: pytest.MonkeyPatch, multitenancy: bool
) -> None:
    _auth_env(monkeypatch, multitenancy=multitenancy)
    factory = ConnectorFactory()
    factory.store.create(
        DEFAULT_TENANT,
        "slack",
        {"name": "keep", "default": True, "config": {}},
    )
    client = _client(factory)
    headers = _headers(**{"X-Tenant-ID": DEFAULT_TENANT})
    body = {"name": "keep", "config": {}}
    created = client.post("/v1/connectors/slack/configs", json=body, headers=headers)
    assert created.status_code == 400
    updated = client.put("/v1/connectors/slack/configs/keep", json=body, headers=headers)
    assert updated.status_code == 400
    deleted = client.delete("/v1/connectors/slack/configs/keep", headers=headers)
    assert deleted.status_code == 400
    made_default = client.put("/v1/connectors/slack/configs/keep/default", headers=headers)
    assert made_default.status_code == 400
    secrets = client.put(
        "/v1/connectors/slack/secrets",
        json={"config_name": "keep", "secrets": {"SLACK_BOT_TOKEN": "xoxb-abc"}},
        headers=headers,
    )
    assert secrets.status_code == 400
    app.dependency_overrides.clear()


def test_named_tenant_config_write_still_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth_env(monkeypatch, multitenancy=True)
    factory = ConnectorFactory()
    client = _client(factory)
    resp = client.post(
        "/v1/connectors/slack/configs",
        json={"name": "primary", "default": True, "config": {}},
        headers=_headers(**{"X-Tenant-ID": "acme"}),
    )
    assert resp.status_code == 201, resp.text
    app.dependency_overrides.clear()


def test_update_rejects_raw_env_secret_ref(factory: ConnectorFactory) -> None:
    client = _client(factory)
    headers = _headers(**{"X-Tenant-ID": "acme"})
    created = client.post(
        f"/v1/connectors/{_CONNECTOR}/configs", json=_doc(_LOGICAL), headers=headers
    )
    assert created.status_code == 201, created.text
    updated = client.put(
        f"/v1/connectors/{_CONNECTOR}/configs/live",
        json=_doc("NW_REST_JWT_SECRET"),
        headers=headers,
    )
    assert updated.status_code == 400, updated.text
    assert "NW_REST_JWT_SECRET" not in updated.text
    assert factory.store.resolve("acme", _CONNECTOR, "live").raw["auth"]["secret_key"] == _LOGICAL


@pytest.mark.parametrize(
    "ref",
    [
        "NW_PROXY_SECRET",
        "NW_MCP_OAUTH_CLIENT_SECRET",
        "NW_GRPC_API_KEY",
        "NW_ACME_SLACK_LIVE_SLACK_BOT_TOKEN",
        "SLACK_BOT_TOKEN ",
        "slack_bot_token",
        "",
    ],
)
def test_declared_connector_refuses_non_logical_refs(ref: str) -> None:
    with pytest.raises(ValueError, match="not an allowed logical name"):
        assert_secret_ref("slack", ref, field="secret_key")


def test_runtime_namespace_refused_even_when_declared() -> None:
    """Defence in depth: a shape that lists an ingress secret does not make it reachable."""
    declare_secret_shape(
        "sec_greedy", formats={"NW_REST_JWT_SECRET": "opaque_secret", "OK": "opaque_secret"}
    )
    with pytest.raises(ValueError):
        assert_secret_ref("sec_greedy", "NW_REST_JWT_SECRET")
    assert_secret_ref("sec_greedy", "OK")


@pytest.mark.parametrize(
    ("ref", "allowed"),
    [
        ("NW_CFG_SEC_UNDECLARED_TOKEN", True),
        ("nw_cfg_sec_undeclared_token", True),
        ("NW_CFG_SLACK_TOKEN", False),
        ("NW_REST_JWT_SECRET", False),
        ("TOKEN", False),
    ],
)
def test_undeclared_connector_is_held_to_its_cfg_prefix(ref: str, allowed: bool) -> None:
    if allowed:
        assert_secret_ref("sec_undeclared", ref)
    else:
        with pytest.raises(ValueError):
            assert_secret_ref("sec_undeclared", ref)


def test_caller_supplied_secret_key_is_gated_at_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slack's ``token_secret_key`` action param names a secret; the lookup is gated too."""
    _auth_env(monkeypatch, multitenancy=False)
    factory = ConnectorFactory()
    factory.store.create(
        DEFAULT_TENANT,
        "slack",
        {
            "name": "keep",
            "default": True,
            "auth": {"provider": "static_token", "secret_key": "SLACK_BOT_TOKEN"},
        },
    )
    record = factory.store.resolve(DEFAULT_TENANT, "slack", None)
    connector = factory._instantiate(record)
    with pytest.raises(ValueError, match="not an allowed logical name"):
        connector.secret_provider.get_secret("NW_REST_JWT_SECRET")


def test_load_tenants_skips_invalid_persisted_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A doc persisted before validation existed is dropped, not loaded, and boot continues."""
    _declare()
    monkeypatch.delenv("NW_REST_ALLOWED_HOSTS", raising=False)
    tenants = tmp_path / "tenants.yaml"
    tenants.write_text(
        yaml.safe_dump(
            {
                "tenants": {
                    "acme": {
                        _CONNECTOR: [
                            {**_doc(_LOGICAL), "name": "good"},
                            {**_doc("NW_REST_JWT_SECRET"), "name": "bad", "default": False},
                            {
                                **_doc(_LOGICAL, base_url="https://evil.example"),
                                "name": "evil",
                                "default": False,
                            },
                        ]
                    },
                    DEFAULT_TENANT: {
                        _CONNECTOR: [{**_doc("NW_ACME_SEC_DEMO_REST_LIVE_DEMO_REST_TOKEN")}]
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NW_TENANTS_PATH", str(tenants))
    store = ConnectorConfigStore()

    load_tenants(store)

    assert store.resolve("acme", _CONNECTOR, "good").name == "good"
    for name in ("bad", "evil"):
        with pytest.raises(ConfigNotFoundError):
            store.resolve("acme", _CONNECTOR, name)
    with pytest.raises(ConfigNotFoundError):
        store.resolve(DEFAULT_TENANT, _CONNECTOR, None)


def test_init_refuses_default_tenant_and_keeps_yaml_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _auth_env(monkeypatch, multitenancy=True)
    factory = ConnectorFactory()
    client = _client(factory)
    slack_doc = {"slack": [{"name": "boot", "default": True, "config": {}}]}

    headed = client.post(
        "/v1/config/init",
        json=slack_doc,
        headers=_headers(**{"X-Tenant-ID": DEFAULT_TENANT}),
    )
    assert headed.status_code == 400, headed.text

    # A full (headerless) init needs platform admin before __default__ is even considered.
    monkeypatch.setenv("NW_REST_API_KEY_SCOPES", f"{_SCOPE} nw:platform:admin")
    bare = client.post(
        "/v1/config/init",
        json={DEFAULT_TENANT: slack_doc},
        headers=_headers(),
    )
    assert bare.status_code == 400, bare.text

    # connectors.yaml bootstrap: init for a named tenant must neither fail nor drop it.
    factory.store.create(
        DEFAULT_TENANT,
        "slack",
        {"name": "keep", "default": True, "config": {}},
    )
    kept = client.post(
        "/v1/config/init",
        json=slack_doc,
        headers=_headers(**{"X-Tenant-ID": "acme"}),
    )
    assert kept.status_code == 200, kept.text
    assert factory.store.resolve("acme", "slack", None).name == "boot"
    assert factory.store.resolve(DEFAULT_TENANT, "slack", None).name == "keep"
    app.dependency_overrides.clear()
