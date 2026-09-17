#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import Awaitable, Callable

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
from node_wire_runtime.mcp_client.oauth_flow import OAuthTokenSet
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


def _manager(
    *,
    store: InMemoryTokenStore | None = None,
    reauthorize: Callable[[], Awaitable[OAuthTokenSet]] | None = None,
) -> TokenManager:
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
        reauthorize=reauthorize,
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


@pytest.mark.asyncio
async def test_concurrent_get_bearer_token_issues_one_refresh() -> None:
    """Racing callers must share one refresh, not each POST the same refresh token.

    Models an IdP that rotates single-use refresh tokens: the second POST of an
    already-spent token is rejected with invalid_grant, which discards the
    partition and forces a full re-authorization.
    """
    store = InMemoryTokenStore()
    reauth_calls = 0

    async def _reauthorize() -> OAuthTokenSet:
        # Stands in for the interactive loopback flow. Without it the unfixed code
        # blocks forever here waiting on a browser instead of failing an assert.
        nonlocal reauth_calls
        reauth_calls += 1
        return OAuthTokenSet(
            access_token="reauthorized",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="rt-reauth",
            scope=None,
        )

    mgr = _manager(store=store, reauthorize=_reauthorize)
    expired = stored_from_oauth_response(
        user_id="alice",
        mcp_server_url="https://mcp.example.com/mcp",
        issuer="https://issuer.example",
        access_token="old",
        token_type="Bearer",
        expires_in=1,
        refresh_token="rt1",
        scope=None,
    )
    expired.expires_at = time.time() - 10
    mgr.save_tokens(expired)

    posts: list[str] = []
    spent: set[str] = set()

    async def handler(request: httpx.Request) -> httpx.Response:
        body = dict(urllib.parse.parse_qsl(request.content.decode()))
        presented = body["refresh_token"]
        posts.append(presented)
        # Yield control so every racing caller is in flight at once — without an
        # await point here the coroutines would serialize by accident.
        await asyncio.sleep(0)
        if presented in spent:
            return httpx.Response(400, json={"error": "invalid_grant"})
        spent.add(presented)
        return httpx.Response(
            200,
            json={
                "access_token": "new",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "rt2",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tokens = await asyncio.wait_for(
            asyncio.gather(*(mgr.get_bearer_token(http_client=client) for _ in range(5))),
            timeout=10,
        )

    assert posts == ["rt1"], f"expected a single refresh exchange, got {posts}"
    assert tokens == ["new"] * 5
    assert reauth_calls == 0, "a redundant refresh discarded the partition"


@pytest.mark.asyncio
async def test_refresh_tokens_skips_exchange_when_another_caller_won() -> None:
    """The 401 path in client.py loads `stored` outside the lock; a caller that
    arrives with a superseded token set gets the newer one, not an invalid_grant."""
    store = InMemoryTokenStore()
    mgr = _manager(store=store)
    stale = stored_from_oauth_response(
        user_id="alice",
        mcp_server_url="https://mcp.example.com/mcp",
        issuer="https://issuer.example",
        access_token="old",
        token_type="Bearer",
        expires_in=1,
        refresh_token="rt1",
        scope=None,
    )
    mgr.save_tokens(stale)
    # Another caller refreshed in between, as client.py's 401 path allows.
    mgr.save_tokens(
        stored_from_oauth_response(
            user_id="alice",
            mcp_server_url="https://mcp.example.com/mcp",
            issuer="https://issuer.example",
            access_token="new",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="rt2",
            scope=None,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no token request should be made")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await mgr.refresh_tokens(stale, http_client=client)

    assert result.access_token == "new"
