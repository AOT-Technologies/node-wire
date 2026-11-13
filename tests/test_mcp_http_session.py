#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Streamable-HTTP session handshake shared by McpOAuthClient and ToolHiveMcpClient."""

from __future__ import annotations

import asyncio
import json
from typing import Callable
from unittest.mock import patch

import httpx
import pytest

from agents.toolhive import ToolHiveMcpClient
from node_wire_runtime.mcp_client.client import McpOAuthClient
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
from node_wire_runtime.mcp_client.session import McpSessionClosedError
from node_wire_runtime.mcp_client.token_manager import TokenManager
from node_wire_runtime.mcp_client.token_storage import (
    InMemoryTokenStore,
    stored_from_oauth_response,
)

MCP = "https://mcp.example.com/mcp"
ISSUER = "https://issuer.example"


class _Server:
    """Fake streamable-HTTP MCP server issuing numbered sessions."""

    def __init__(self) -> None:
        self.initializes = 0
        self.live: set[str] = set()
        self.expire_next_call = 0
        self.fail_next_initialize = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        sid = request.headers.get("Mcp-Session-Id")
        if method == "initialize":
            await asyncio.sleep(0.01)  # let racing callers pile up
            if self.fail_next_initialize:
                self.fail_next_initialize = False
                return httpx.Response(500)
            self.initializes += 1
            new = f"s{self.initializes}"
            self.live.add(new)
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
                headers={"Mcp-Session-Id": new},
            )
        if sid not in self.live:
            return httpx.Response(404)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if self.expire_next_call:
            self.expire_next_call -= 1
            self.live.discard(sid)
            return httpx.Response(404)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": sid}]}}
        )


def _oauth_client(server: _Server) -> McpOAuthClient:
    config = McpClientConfig(
        server=McpServerConfig(url=MCP),
        auth=AuthConfig(client=AuthClientConfig(id="cid")),
    )
    discovery = DiscoveryResult(
        mcp_server_url=MCP,
        protected_resource=ProtectedResourceMetadata(
            resource=MCP, authorization_servers=(ISSUER,), raw={}
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
    store = InMemoryTokenStore()
    store.save(
        stored_from_oauth_response(
            user_id="u",
            mcp_server_url=MCP,
            issuer=ISSUER,
            access_token="tok",
            token_type="Bearer",
            expires_in=3600,
            refresh_token=None,
            scope=None,
        )
    )
    mgr = TokenManager(config, user_id="u", token_store=store, discovery=discovery)
    http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    return McpOAuthClient(MCP, config=config, user_id="u", token_manager=mgr, http_client=http)


def _toolhive_client(server: _Server):
    real = httpx.AsyncClient

    def make(**kwargs):
        return real(transport=httpx.MockTransport(server.handle))

    patcher = patch("httpx.AsyncClient", side_effect=make)
    patcher.start()
    client = ToolHiveMcpClient(MCP)
    client._test_patcher = patcher  # type: ignore[attr-defined]
    return client


FACTORIES: list[Callable[[_Server], object]] = [_oauth_client, _toolhive_client]


@pytest.fixture(params=FACTORIES, ids=["oauth", "toolhive"])
def make_client(request):
    made = []

    def factory(server: _Server):
        c = request.param(server)
        made.append(c)
        return c

    yield factory
    for c in made:
        patcher = getattr(c, "_test_patcher", None)
        if patcher is not None:
            patcher.stop()


@pytest.mark.asyncio
async def test_concurrent_first_calls_initialize_once(make_client) -> None:
    server = _Server()
    client = make_client(server)
    results = await asyncio.gather(*(client.list_tools() for _ in range(5)))
    assert server.initializes == 1
    assert all(r == [{"name": "s1"}] for r in results)
    await client.aclose()


@pytest.mark.asyncio
async def test_expired_session_reinitializes_and_retries_once(make_client) -> None:
    server = _Server()
    client = make_client(server)
    await client.list_tools()
    server.expire_next_call = 1
    assert await client.list_tools() == [{"name": "s2"}]
    assert server.initializes == 2
    # Expiring twice in a row is not retried forever.
    server.expire_next_call = 2
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_tools()
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_initialize_can_be_retried(make_client) -> None:
    server = _Server()
    client = make_client(server)
    server.fail_next_initialize = True
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_tools()
    assert await client.list_tools() == [{"name": "s1"}]
    await client.aclose()


@pytest.mark.asyncio
async def test_calls_after_aclose_raise(make_client) -> None:
    server = _Server()
    client = make_client(server)
    await client.list_tools()
    await client.aclose()
    with pytest.raises(McpSessionClosedError):
        await client.list_tools()
    with pytest.raises(McpSessionClosedError):
        await client.call_tool("x", {})
