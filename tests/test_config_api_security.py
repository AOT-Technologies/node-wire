#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Runtime config documents cannot name process env vars or retarget a connector host."""

from __future__ import annotations

import json
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from bindings.factory import ConnectorFactory
from bindings.invoke import invoke
from bindings.rest_api.app import app, get_factory
from node_wire_runtime import nw_action
from node_wire_runtime.config_store import DEFAULT_TENANT
from node_wire_runtime.rest import RestConnector, RestResponseOutput
from node_wire_runtime.secrets import tenant_scoped_secret_key
from node_wire_runtime.tenant_persistence import declare_secret_shape

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
    assert (
        client.post("/v1/connectors/slack/configs", json=body, headers=headers).status_code == 400
    )
    assert (
        client.put("/v1/connectors/slack/configs/keep", json=body, headers=headers).status_code
        == 400
    )
    assert client.delete("/v1/connectors/slack/configs/keep", headers=headers).status_code == 400
    assert (
        client.put("/v1/connectors/slack/configs/keep/default", headers=headers).status_code == 400
    )
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


def test_init_rejects_default_tenant_and_keeps_empty_store_named_init(
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

    bare = client.post(
        "/v1/config/init",
        json={DEFAULT_TENANT: slack_doc},
        headers=_headers(),
    )
    assert bare.status_code == 400, bare.text

    factory.store.create(
        DEFAULT_TENANT,
        "slack",
        {"name": "keep", "default": True, "config": {}},
    )
    drops = client.post(
        "/v1/config/init",
        json=slack_doc,
        headers=_headers(**{"X-Tenant-ID": "acme"}),
    )
    assert drops.status_code == 400, drops.text

    empty = ConnectorFactory()
    app.dependency_overrides.clear()
    kept_client = _client(empty)
    kept = kept_client.post(
        "/v1/config/init",
        json=slack_doc,
        headers=_headers(**{"X-Tenant-ID": "acme"}),
    )
    assert kept.status_code == 200, kept.text
    assert empty.store.resolve("acme", "slack", None).name == "boot"
    app.dependency_overrides.clear()
