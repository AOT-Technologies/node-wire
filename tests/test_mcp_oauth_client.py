#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json

import pytest
import httpx

from node_wire_runtime.mcp_client.client import McpOAuthClient, create_http_mcp_client
from node_wire_runtime.mcp_client.config import (
    AuthClientConfig,
    AuthConfig,
    McpClientConfig,
    McpServerConfig,
)
from node_wire_runtime.mcp_client.discovery import (
    AuthorizationServerMetadata,
    DiscoveryResult,
    ProtectedResourceMetadata,
)
from node_wire_runtime.mcp_client.oauth_flow import AuthorizationCodeFlow
from node_wire_runtime.mcp_client.storage import ClientRegistration
from node_wire_runtime.mcp_client.token_manager import TokenManager
from node_wire_runtime.mcp_client.token_storage import (
    InMemoryTokenStore,
    stored_from_oauth_response,
)


MCP_BASE = "https://mcp.example.com/mcp"
ISSUER = "https://issuer.example"


def _discovery() -> DiscoveryResult:
    return DiscoveryResult(
        mcp_server_url=MCP_BASE,
        protected_resource=ProtectedResourceMetadata(
            resource=MCP_BASE,
            authorization_servers=(ISSUER,),
            raw={},
        ),
        authorization_server=AuthorizationServerMetadata(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/authorize",
            token_endpoint=f"{ISSUER}/token",
            registration_endpoint=None,
            scopes_supported=None,
            raw={},
        ),
        issuer=ISSUER,
    )


def _oauth_client(*, handler) -> McpOAuthClient:
    config = McpClientConfig(
        server=McpServerConfig(url=MCP_BASE),
        auth=AuthConfig(client=AuthClientConfig(id="cid", secret="")),
    )
    reg = ClientRegistration(
        issuer=ISSUER,
        client_id="cid",
        client_secret=None,
        redirect_uris=("http://127.0.0.1:1/callback",),
        token_endpoint_auth_method="none",
        registered_at="",
    )
    store = InMemoryTokenStore()
    store.save(
        stored_from_oauth_response(
            user_id="demo",
            mcp_server_url=MCP_BASE,
            issuer=ISSUER,
            access_token="valid-token",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="rt",
            scope=None,
        )
    )
    flow = AuthorizationCodeFlow(config, discovery=_discovery(), registration=reg)
    mgr = TokenManager(
        config,
        user_id="demo",
        token_store=store,
        discovery=_discovery(),
        registration=reg,
        auth_flow=flow,
    )
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return McpOAuthClient(
        MCP_BASE,
        config=config,
        user_id="demo",
        token_manager=mgr,
        http_client=http,
    )


@pytest.mark.asyncio
async def test_mcp_oauth_client_list_tools_with_bearer() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization"))
        body = json.loads(request.content) if request.content else {}
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2024-11-05"},
                },
                headers={"Mcp-Session-Id": "sess-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "demo.tool"}]},
                },
            )
        return httpx.Response(404)

    client = _oauth_client(handler=handler)
    tools = await client.list_tools()
    await client.aclose()
    assert tools[0]["name"] == "demo.tool"
    assert calls[0] == "Bearer valid-token"


@pytest.mark.asyncio
async def test_mcp_oauth_client_401_refresh_retry_once() -> None:
    list_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal list_calls
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "refreshed",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt2",
                },
            )
        body = json.loads(request.content) if request.content else {}
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2024-11-05"},
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(200, json={"jsonrpc": "2.0", "result": {}})
        if method == "tools/list":
            list_calls += 1
            if list_calls == 1:
                return httpx.Response(
                    401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": []},
                },
            )
        return httpx.Response(404)

    client = _oauth_client(handler=handler)
    tools = await client.list_tools()
    await client.aclose()
    assert tools == []
    assert list_calls == 2


def test_create_http_mcp_client_legacy_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from agents.toolhive import ToolHiveMcpClient

    monkeypatch.setenv("TOOLHIVE_MCP_BEARER_TOKEN", "static")
    monkeypatch.delenv("NW_MCP_OAUTH_ENABLED", raising=False)
    client = create_http_mcp_client("http://localhost/mcp")
    assert isinstance(client, ToolHiveMcpClient)


def test_create_http_mcp_client_oauth_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOOLHIVE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TOOLHIVE_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_OAUTH_ENABLED", "true")
    client = create_http_mcp_client("https://mcp.example.com/mcp")
    assert isinstance(client, McpOAuthClient)
