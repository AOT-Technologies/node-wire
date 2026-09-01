#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Playground agent chat forwards tenant to MCP clients."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.toolhive import InProcessMcpClient, ToolHiveMcpClient
from node_wire_runtime.tenant_session import TenantSessionOverlay


def _fake_tenant_session() -> TenantSessionOverlay:
    """A TenantSessionOverlay with a permissive store, for MagicMock servers
    that need real (not auto-mocked) session-pin state."""
    return TenantSessionOverlay(
        store_has_tenant=lambda tenant_id: True,
        config_coverage=lambda tenant_id, config_name: (["c1"], []),
    )


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
    server.tenant_session = _fake_tenant_session()
    server.list_tools.return_value = [{"name": "http_generic.request", "input_schema": {}}]

    async def fake_invoke(name, arguments, identity=None):
        return {"ok": True, "args": arguments}

    server.invoke_tool = fake_invoke

    client = InProcessMcpClient(server, tenant_id="acme", config_name="primary")
    async with client:
        assert server.tenant_session.env_pin == "acme"
        tools = await client.list_tools()
        assert tools[0]["name"] == "http_generic.request"
        assert server.tenant_session.selected_tenant_id == "acme"
        assert server.tenant_session.selected_config_name == "primary"
        out = await client.call_tool("http_generic.request", {"url": "https://example.com"})
        data = json.loads(out)
        assert data["args"]["url"] == "https://example.com"
        assert "config_name" not in data["args"]

        out2 = await client.call_tool(
            "http_generic.request",
            {"url": "https://example.com", "config_name": None, "query": None},
        )
        data2 = json.loads(out2)
        assert "config_name" not in data2["args"]
        assert "query" not in data2["args"]


@pytest.mark.asyncio
async def test_inprocess_enter_applies_host_config_overlay() -> None:
    server = MagicMock()
    server.tenant_session = _fake_tenant_session()
    server.tenant_session.set_selected_config("overlay-cfg")

    async def fake_invoke(name, arguments, identity=None):
        return {"ok": True, "args": arguments}

    server.invoke_tool = fake_invoke
    client = InProcessMcpClient(server, tenant_id="acme", config_name="primary")
    async with client:
        assert server.tenant_session.selected_config_name == "primary"
        out = await client.call_tool("http_generic.request", {"url": "https://example.com"})
        data = json.loads(out)
        assert "config_name" not in data["args"]


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


def test_llm_options_endpoint_filters_and_defaults_to_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("NVIDIA_API_KEY", "nk")
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
    from bindings.rest_api.app import app

    client = TestClient(app)
    resp = client.get("/scenarios/llm-options")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_id"] == "groq/openai/gpt-oss-120b"
    assert len(data["options"]) == 2


def test_agent_chat_uses_llm_option(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("NVIDIA_API_KEY", "nk")
    monkeypatch.setenv("NW_MCP_TRANSPORT", "stdio")

    from agents.toolhive import AgentRunResult

    created: list[str | None] = []

    class FakeProvider:
        def chat_with_tools(self, messages, tools):  # noqa: ANN001
            return None

    def fake_create_from_option(llm_option=None):
        created.append(llm_option)
        return FakeProvider()

    async def fake_run(self, task):
        return AgentRunResult(
            success=True, final_answer="hello from agent", steps=[], trace_id="t1"
        )

    class _CM:
        async def __aenter__(self):
            client = MagicMock()
            client._server = None
            return client

        async def __aexit__(self, *args):
            return None

    with (
        patch(
            "agents.llm_factory.LLMProviderFactory.create_from_option",
            side_effect=fake_create_from_option,
        ),
        patch("agents.toolhive.ToolHiveAgent.run", fake_run),
        patch(
            "playground.scenarios._playground_inprocess_mcp_client",
            return_value=_CM(),
        ),
    ):
        from bindings.rest_api.app import app

        client = TestClient(app)
        resp = client.post(
            "/scenarios/agent-chat",
            json={
                "message": "hi",
                "history": [],
                "llm_option": "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["reply"] == "hello from agent"
    assert created == ["nvidia/nvidia/nemotron-3.5-lightning-30b-a3b"]


def test_tenancy_from_agent_steps_selects_tenant_and_config() -> None:
    from playground.scenarios import _tenancy_from_agent_steps

    tenant, config = _tenancy_from_agent_steps(
        [
            {"tool": "nw_select_tenant", "args": {"tenant_id": "acme-demo"}},
            {"tool": "nw_select_config", "args": {"config_name": "test-work"}},
        ],
        "acme",
        "primary",
    )
    assert tenant == "acme-demo"
    assert config == "test-work"


def test_tenancy_from_mcp_client_prefers_overlay_selection() -> None:
    from node_wire_runtime.tenant_session import TenantSessionOverlay
    from playground.scenarios import _tenancy_from_mcp_client

    tenant_session = TenantSessionOverlay(
        store_has_tenant=lambda tid: True,
        config_coverage=lambda tid, name: (["c1"], []),
    )
    tenant_session.set_selected_tenant("acme-demo")
    tenant_session.set_selected_config("alt")

    server = SimpleNamespace(tenant_session=tenant_session)
    client = SimpleNamespace(_server=server)

    tenant, config = _tenancy_from_mcp_client(client, "acme", "primary")
    assert tenant == "acme-demo"
    assert config == "alt"


def test_tenancy_from_mcp_client_falls_back_when_nothing_selected() -> None:
    from node_wire_runtime.tenant_session import TenantSessionOverlay
    from playground.scenarios import _tenancy_from_mcp_client

    tenant_session = TenantSessionOverlay(
        store_has_tenant=lambda tid: True,
        config_coverage=lambda tid, name: ([], []),
    )
    server = SimpleNamespace(tenant_session=tenant_session)
    client = SimpleNamespace(_server=server)

    tenant, config = _tenancy_from_mcp_client(client, "acme", "primary")
    assert tenant == "acme"
    assert config == "primary"


def test_tenancy_from_mcp_client_without_server_attr_passes_through() -> None:
    from playground.scenarios import _tenancy_from_mcp_client

    tenant, config = _tenancy_from_mcp_client(SimpleNamespace(), "acme", "primary")
    assert tenant == "acme"
    assert config == "primary"
