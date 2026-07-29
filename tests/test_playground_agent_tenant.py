#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Playground agent chat forwards tenant to MCP clients."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.toolhive import InProcessMcpClient, ToolHiveMcpClient


@pytest.fixture(autouse=True)
def _agent_tenant_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS",
        "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner",
    )
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")
    monkeypatch.delenv("TOOLHIVE_MCP_URL", raising=False)
    monkeypatch.delenv("TOOLHIVE_MCP_URLS", raising=False)
    monkeypatch.setenv("NW_MCP_TRANSPORT", "stdio")


@pytest.mark.asyncio
async def test_toolhive_client_sends_tenant_header() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({k.lower(): v for k, v in request.headers.items()})
        body = json.loads(request.content.decode())
        method = body.get("method")
        req_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05"}},
                headers={"Mcp-Session-Id": "s1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(200, json={})
        if method == "tools/list":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_id, "result": {"tools": []}},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    _Real = httpx.AsyncClient

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return _Real(transport=transport, timeout=60.0)

    with patch("httpx.AsyncClient", side_effect=make_client):
        client = ToolHiveMcpClient(
            "http://127.0.0.1:9/mcp",
            extra_headers={"x-tenant-id": "acme"},
        )
        await client.list_tools()

    assert any(h.get("x-tenant-id") == "acme" for h in seen)


@pytest.mark.asyncio
async def test_inprocess_mcp_client_pins_tenant_and_config_name() -> None:
    server = MagicMock()
    server.list_tools.return_value = [{"name": "http_generic.request", "input_schema": {}}]

    async def fake_invoke(name, arguments, identity=None):
        return {"ok": True, "args": arguments}

    server.invoke_tool = fake_invoke

    client = InProcessMcpClient(server, tenant_id="acme", config_name="primary")
    async with client:
        assert server._stdio_env_tenant_pin == "acme"
        tools = await client.list_tools()
        assert tools[0]["name"] == "http_generic.request"
        out = await client.call_tool("http_generic.request", {"url": "https://example.com"})
        data = json.loads(out)
        assert data["args"]["config_name"] == "primary"
        assert data["args"]["url"] == "https://example.com"

        # Null from LLM must not block host-injected config_name.
        out2 = await client.call_tool(
            "http_generic.request",
            {"url": "https://example.com", "config_name": None, "query": None},
        )
        data2 = json.loads(out2)
        assert data2["args"]["config_name"] == "primary"
        assert "query" not in data2["args"]


def test_agent_chat_requires_tenant_when_mt_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    from bindings.rest_api.app import app

    client = TestClient(app)
    resp = client.post(
        "/scenarios/agent-chat",
        json={"message": "hello", "history": []},
    )
    assert resp.status_code == 400
    assert "X-Tenant-ID" in resp.json().get("detail", "")
