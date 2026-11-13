#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The call check: a hidden action is refused in every binding before its connector exists."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import mcp.types as types
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bindings.mcp_server.server as server_mod
import bindings.rest_api.app as rest_app
from bindings.grpc_server import connector_pb2
from bindings.grpc_server.server import ConnectorServiceServicer
from bindings.invoke import ActionNotExposed, invoke
from node_wire_runtime import ConnectorResponse
from node_wire_runtime.exposure import ActionExposure

# Exposes post_message only; delete_message is hidden.
_ONLY_POST = ActionExposure({"slack": frozenset({"post_message"})}, profile="p")


def _factory() -> MagicMock:
    factory = MagicMock()
    factory.is_exposed.return_value = True
    connector = MagicMock()
    connector.run = AsyncMock(return_value=ConnectorResponse(success=True, data={}, trace_id="t"))
    factory.get = AsyncMock(return_value=connector)
    return factory


async def test_invoke_hidden_action_never_reaches_the_factory() -> None:
    factory = _factory()
    with pytest.raises(ActionNotExposed, match="delete_message"):
        await invoke(
            factory,
            connector_id="slack",
            action="delete_message",
            payload={},
            protocol="rest",
            tenant_id="__default__",
            exposure=_ONLY_POST,
        )
    factory.get.assert_not_called()


async def test_invoke_exposed_action_runs() -> None:
    resp = await invoke(
        _factory(),
        connector_id="slack",
        action="post_message",
        payload={},
        protocol="rest",
        tenant_id="__default__",
        exposure=_ONLY_POST,
    )
    assert resp.success is True


def test_rest_hidden_action_is_404_action_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    # A route that exists (http_generic is registered), with its one action hidden.
    monkeypatch.setattr(
        rest_app, "_exposure", ActionExposure({"http_generic": frozenset()}, profile="p")
    )
    factory = _factory()
    rest_app.app.dependency_overrides[rest_app.get_factory] = lambda: factory
    try:
        resp = TestClient(rest_app.app).post("/connectors/http_generic/request", json={})
    finally:
        rest_app.app.dependency_overrides.clear()
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "ACTION_NOT_EXPOSED"
    factory.get.assert_not_called()


async def test_grpc_hidden_action_is_refused() -> None:
    factory = _factory()
    servicer = ConnectorServiceServicer(factory=factory, exposure=_ONLY_POST)
    resp = await servicer._invoke_async(
        connector_pb2.InvokeRequest(connector_id="slack", action="delete_message")
    )
    assert resp.success is False
    assert resp.error_code == "ACTION_NOT_EXPOSED"
    assert resp.error_category == "BUSINESS"
    factory.get.assert_not_called()


def _smtp_server(monkeypatch: pytest.MonkeyPatch, allowed: frozenset[str]) -> server_mod.McpServer:
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp")
    return server_mod.McpServer(
        connector_ids=["smtp"], exposure=ActionExposure({"smtp": allowed}, profile="p")
    )


def test_rest_routes_exist_only_for_exposed_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest_app, "app", FastAPI())
    monkeypatch.setattr(
        rest_app, "_exposure", ActionExposure({"http_generic": frozenset()}, profile="p")
    )
    rest_app._build_dynamic_routes()
    assert not [r for r in rest_app.app.routes if "http_generic" in r.path]
    monkeypatch.setattr(rest_app, "app", FastAPI())
    monkeypatch.setattr(rest_app, "_exposure", ActionExposure.unrestricted())
    rest_app._build_dynamic_routes()
    assert any(r.path == "/connectors/http_generic/request" for r in rest_app.app.routes)


def test_mcp_manifest_lists_only_exposed_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    everything = _smtp_server(monkeypatch, frozenset())._factory.list_for_protocol("mcp")
    assert everything  # smtp is loaded
    visible = {
        e["action"] for e in _smtp_server(monkeypatch, frozenset({"send_email"}))._manifest()[0]
    }
    assert visible == {"send_email"}
    assert _smtp_server(monkeypatch, frozenset())._manifest()[0] == []


async def test_mcp_hidden_action_is_isError(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exposes nothing on smtp, so every smtp action is hidden.
    server = _smtp_server(monkeypatch, frozenset())
    tool = "smtp_send_email"
    low = server._setup_lowlevel_server()
    result = (
        await low.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name=tool, arguments={"to": "a@example.com", "subject": "s", "body": "b"}
                ),
            )
        )
    ).root
    assert result.isError is True
    # The listing no longer has the tool, so the name is unknown; invoke() is the backstop.
    assert result.structuredContent["error_code"] == "UNKNOWN_TOOL"
