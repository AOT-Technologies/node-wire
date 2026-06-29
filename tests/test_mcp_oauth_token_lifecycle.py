#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import time

import pytest
import httpx

from node_wire_runtime.mcp_client.config import (
    AuthClientConfig,
    AuthConfig,
    AuthTokenConfig,
    McpClientConfig,
    McpServerConfig,
)
from node_wire_runtime.mcp_client.discovery import (
    AuthorizationServerMetadata,
    DiscoveryResult,
    ProtectedResourceMetadata,
)
from node_wire_runtime.mcp_client.exceptions import McpAudienceMismatch, McpTokenRefreshError
from node_wire_runtime.mcp_client.storage import ClientRegistration
from node_wire_runtime.mcp_client.token_manager import TokenManager
from node_wire_runtime.mcp_client.token_storage import (
    InMemoryTokenStore,
    stored_from_oauth_response,
)


def _discovery() -> DiscoveryResult:
    return DiscoveryResult(
        mcp_server_url="https://mcp.example.com/mcp",
        protected_resource=ProtectedResourceMetadata(
            resource="https://mcp.example.com/mcp",
            authorization_servers=("https://issuer.example",),
            raw={},
        ),
        authorization_server=AuthorizationServerMetadata(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            registration_endpoint=None,
            scopes_supported=None,
            raw={},
        ),
        issuer="https://issuer.example",
    )


def _config() -> McpClientConfig:
    return McpClientConfig(
        server=McpServerConfig(url="https://mcp.example.com/mcp"),
        auth=AuthConfig(
            client=AuthClientConfig(id="cid", secret=""),
            token=AuthTokenConfig(refresh_lead_seconds=60),
        ),
    )


def _manager(*, store: InMemoryTokenStore | None = None) -> TokenManager:
    reg = ClientRegistration(
        issuer="https://issuer.example",
        client_id="cid",
        client_secret=None,
        redirect_uris=("http://127.0.0.1:1/callback",),
        token_endpoint_auth_method="none",
        registered_at="",
    )
    from node_wire_runtime.mcp_client.oauth_flow import AuthorizationCodeFlow

    flow = AuthorizationCodeFlow(_config(), discovery=_discovery(), registration=reg)
    return TokenManager(
        _config(),
        user_id="alice",
        token_store=store or InMemoryTokenStore(),
        discovery=_discovery(),
        registration=reg,
        auth_flow=flow,
    )


@pytest.mark.asyncio
async def test_proactive_refresh_before_expiry() -> None:
    store = InMemoryTokenStore()
    mgr = _manager(store=store)
    stored = stored_from_oauth_response(
        user_id="alice",
        mcp_server_url="https://mcp.example.com/mcp",
        issuer="https://issuer.example",
        access_token="old",
        token_type="Bearer",
        expires_in=30,
        refresh_token="rt",
        scope=None,
    )
    stored.expires_at = time.time() + 30
    mgr.save_tokens(stored)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "new",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt2",
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        token = await mgr.get_bearer_token(http_client=client)
    assert token == "new"


@pytest.mark.asyncio
async def test_refresh_invalid_grant_discards_and_raises() -> None:
    store = InMemoryTokenStore()
    mgr = _manager(store=store)
    mgr.save_tokens(
        stored_from_oauth_response(
            user_id="alice",
            mcp_server_url="https://mcp.example.com/mcp",
            issuer="https://issuer.example",
            access_token="old",
            token_type="Bearer",
            expires_in=1,
            refresh_token="bad",
            scope=None,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(McpTokenRefreshError):
            await mgr.refresh_tokens(mgr.load_stored(), http_client=client)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_handle_mcp_403_forbidden() -> None:
    mgr = _manager()
    action = await mgr.handle_mcp_response(403, None)
    assert action == "forbidden"


@pytest.mark.asyncio
async def test_handle_mcp_401_invalid_token_retries() -> None:
    mgr = _manager()
    mgr.save_tokens(
        stored_from_oauth_response(
            user_id="alice",
            mcp_server_url="https://mcp.example.com/mcp",
            issuer="https://issuer.example",
            access_token="stale",
            token_type="Bearer",
            expires_in=3600,
            refresh_token=None,
            scope=None,
        )
    )
    action = await mgr.handle_mcp_response(
        401,
        'Bearer error="invalid_token"',
    )
    assert action == "retry"
    assert mgr.load_stored() is not None


def test_jwt_audience_mismatch() -> None:
    import jwt

    mgr = _manager()
    token = jwt.encode(
        {"aud": "https://other.example/mcp"},
        "test-secret-key-32-bytes-min!!",
        algorithm="HS256",
    )
    with pytest.raises(McpAudienceMismatch):
        mgr.validate_access_token_audience(token)
