#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""``GET /playground/connectors``: what the playground lists without a panel of its own."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bindings.factory import ConnectorFactory
from bindings.rest_api import app as rest_app
from node_wire_http_generic.logic import HttpGenericConnector
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.tenant_persistence import REQUIRED_SECRETS_BY_CONNECTOR

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _mounted_client(monkeypatch: pytest.MonkeyPatch, factory: MagicMock) -> TestClient:
    monkeypatch.setattr(rest_app, "get_factory", lambda: factory)
    monkeypatch.setenv("NW_REST_PLAYGROUND_ENABLED", "true")
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    app = FastAPI()
    rest_app._mount_playground(app)
    return TestClient(app)


def _factory_with(connector: HttpGenericConnector) -> MagicMock:
    factory = MagicMock()
    factory.list_for_protocol.return_value = [connector]
    factory.config_template.return_value = {"config": {}, "auth": {}}
    return factory


def test_lists_rest_connectors_with_action_input_schemas(monkeypatch: pytest.MonkeyPatch):
    connector = HttpGenericConnector()
    factory = _factory_with(connector)
    client = _mounted_client(monkeypatch, factory)

    resp = client.get("/playground/connectors")

    assert resp.status_code == 200
    actions = {a["action"]: a for a in resp.json()["http_generic"]["actions"]}
    assert set(actions) == set(type(connector).sdk_action_metas())
    schema = actions["request"]["input_schema"]
    assert schema["type"] == "object"
    assert "action" not in schema["properties"]
    factory.list_for_protocol.assert_called_once_with("rest")
    # The static mount must not shadow the route.
    assert client.get("/playground/").status_code == 200


def test_gives_the_add_config_dialog_its_template_and_secret_names(
    monkeypatch: pytest.MonkeyPatch,
):
    factory = _factory_with(HttpGenericConnector())
    factory.config_template.return_value = {
        "config": {"base_url": "https://api.example.com"},
        "auth": {"provider": "static_token", "secret_key": "EXAMPLE_TOKEN"},
    }
    monkeypatch.setitem(REQUIRED_SECRETS_BY_CONNECTOR, "http_generic", ["EXAMPLE_TOKEN"])
    client = _mounted_client(monkeypatch, factory)

    entry = client.get("/playground/connectors").json()["http_generic"]

    assert entry["config_template"] == factory.config_template.return_value
    assert entry["secret_names"] == ["EXAMPLE_TOKEN"]


def test_every_static_token_connector_lists_its_secret_name(monkeypatch: pytest.MonkeyPatch):
    """Real connectors, not mocks: a static-token auth block must come with a declared secret.

    ``secret_names`` comes from ``declare_secret_shape()`` and the auth block from
    ``connectors.yaml``. If a connector (for example a newly generated one) drops the
    declaration, the Add config dialog would show no credential field for it.
    """
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "slack,stripe,github")
    auto_register()
    # conftest points NW_CONFIG_PATH at a test fixture; this test is about the shipped config.
    factory = ConnectorFactory(config_path=_REPO_ROOT / "config" / "connectors.yaml")
    factory.load()
    client = _mounted_client(monkeypatch, factory)

    connectors = client.get("/playground/connectors").json()

    static = {
        cid: entry
        for cid, entry in connectors.items()
        if entry["config_template"]["auth"].get("provider") == "static_token"
    }
    # Guard: the loop below must not pass by finding nothing.
    assert {"slack", "stripe", "github"} <= set(static)
    for cid, entry in static.items():
        assert entry["secret_names"], cid
        assert entry["config_template"]["auth"]["secret_key"] in entry["secret_names"], cid


def test_carries_no_secret_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_should_never_appear")
    client = _mounted_client(monkeypatch, _factory_with(HttpGenericConnector()))

    assert "ghp_should_never_appear" not in client.get("/playground/connectors").text


def test_factory_config_template_splits_auth_from_other_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    yaml_path = tmp_path / "connectors.yaml"
    yaml_path.write_text(
        """connectors:
  slack:
    enabled: true
    exposed_via: [rest]
    base_url: https://api.example.com
    headers:
      Accept: application/json
    auth:
      provider: static_token
      secret_key: SLACK_BOT_TOKEN
    auth_schemes:
      other: {provider: none}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(ConnectorFactory, "_instantiate", lambda self, record: MagicMock())
    # base_url / headers are only accepted for REST connectors; this test is about the split.
    monkeypatch.setattr("node_wire_runtime.config_store._check_base_urls", lambda *a: None)
    monkeypatch.setattr("node_wire_runtime.config_store._check_headers", lambda *a: None)
    factory = ConnectorFactory(config_path=yaml_path)
    factory.load()

    template = factory.config_template("slack")

    assert template["auth"] == {"provider": "static_token", "secret_key": "SLACK_BOT_TOKEN"}
    assert template["config"] == {
        "base_url": "https://api.example.com",
        "headers": {"Accept": "application/json"},
    }
    assert factory.config_template("not_configured") == {"config": {}, "auth": {}}


def test_not_listed_when_the_playground_is_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NW_REST_PLAYGROUND_ENABLED", raising=False)
    app = FastAPI()

    rest_app._mount_playground(app)

    assert TestClient(app).get("/playground/connectors").status_code == 404
