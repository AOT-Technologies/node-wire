#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import pytest
import httpx

from node_wire_runtime.mcp_client.config import McpClientConfig, McpServerConfig
from node_wire_runtime.mcp_client.discovery import (
    DiscoveryCache,
    discover,
    parse_resource_metadata_url,
    protected_resource_metadata_well_known_url,
    select_issuer,
)
from node_wire_runtime.mcp_client.exceptions import McpOAuthDiscoveryError


def _config() -> McpClientConfig:
    return McpClientConfig(server=McpServerConfig(url="https://mcp.example.com/mcp"))


def test_parse_resource_metadata_url() -> None:
    header = 'Bearer error="invalid_token", resource_metadata="https://as.example/prm"'
    assert parse_resource_metadata_url(header) == "https://as.example/prm"
    assert parse_resource_metadata_url(None) is None


def test_protected_resource_well_known_url() -> None:
    url = protected_resource_metadata_well_known_url("https://mcp.example.com/mcp/")
    assert url == "https://mcp.example.com/.well-known/oauth-protected-resource"


def test_select_issuer_override_must_be_listed() -> None:
    with pytest.raises(McpOAuthDiscoveryError):
        select_issuer(
            ("https://issuer.a",),
            override="https://issuer.b",
        )


@pytest.mark.asyncio
async def test_discover_full_chain() -> None:
    prm = {
        "resource": "https://mcp.example.com/mcp",
        "authorization_servers": ["https://issuer.example"],
    }
    as_meta = {
        "issuer": "https://issuer.example",
        "authorization_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "registration_endpoint": "https://issuer.example/register",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "oauth-protected-resource" in request.url.path:
            return httpx.Response(200, json=prm)
        if "oauth-authorization-server" in request.url.path:
            return httpx.Response(200, json=as_meta)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await discover(_config(), cache=DiscoveryCache(3600), http_client=client)

    assert result.issuer == "https://issuer.example"
    assert result.authorization_server.token_endpoint.endswith("/token")
    assert result.protected_resource.authorization_servers[0] == "https://issuer.example"


@pytest.mark.asyncio
async def test_discover_uses_cache() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if "oauth-protected-resource" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.example.com/mcp",
                    "authorization_servers": ["https://issuer.example"],
                },
            )
        return httpx.Response(
            200,
            json={
                "issuer": "https://issuer.example",
                "authorization_endpoint": "https://issuer.example/authorize",
                "token_endpoint": "https://issuer.example/token",
            },
        )

    transport = httpx.MockTransport(handler)
    cache = DiscoveryCache(3600)
    async with httpx.AsyncClient(transport=transport) as client:
        await discover(_config(), cache=cache, http_client=client)
        await discover(_config(), cache=cache, http_client=client)
    assert calls == 2  # PRM + AS once; second discover hits cache
