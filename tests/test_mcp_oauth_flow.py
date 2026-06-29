#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import pytest

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
from node_wire_runtime.mcp_client.oauth_flow import (
    AuthorizationCodeFlow,
    build_authorization_url,
    generate_pkce_pair,
    generate_state,
)
from node_wire_runtime.mcp_client.redirect_listener import AuthorizationCallback
from node_wire_runtime.mcp_client.exceptions import McpOAuthSecurityError
from node_wire_runtime.mcp_client.storage import ClientRegistration


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


def _flow() -> AuthorizationCodeFlow:
    cfg = McpClientConfig(
        server=McpServerConfig(url="https://mcp.example.com/mcp"),
        auth=AuthConfig(client=AuthClientConfig(id="cid", secret="")),
    )
    return AuthorizationCodeFlow(
        cfg,
        discovery=_discovery(),
        registration=ClientRegistration(
            issuer="https://issuer.example",
            client_id="cid",
            client_secret=None,
            redirect_uris=("http://127.0.0.1:1/callback",),
            token_endpoint_auth_method="none",
            registered_at="",
        ),
    )


def test_pkce_s256_challenge() -> None:
    pair = generate_pkce_pair()
    assert 43 <= len(pair.code_verifier) <= 128
    assert pair.code_challenge
    assert "=" not in pair.code_challenge


def test_build_authorization_url_includes_resource() -> None:
    pkce = generate_pkce_pair()
    url = build_authorization_url(
        authorization_endpoint="https://issuer.example/authorize",
        client_id="cid",
        redirect_uri="http://127.0.0.1:1/callback",
        scope="tools",
        state="st",
        pkce=pkce,
        resource="https://mcp.example.com/mcp",
    )
    assert "resource=https%3A%2F%2Fmcp.example.com%2Fmcp" in url
    assert "code_challenge_method=S256" in url


def test_state_entropy() -> None:
    assert len(generate_state()) >= 16


def test_validate_callback_state_mismatch() -> None:
    flow = _flow()
    with pytest.raises(McpOAuthSecurityError, match="state mismatch"):
        flow.validate_callback(
            AuthorizationCallback(code="c", state="wrong", error=None, error_description=None),
            expected_state="expected",
        )


@pytest.mark.asyncio
async def test_exchange_code() -> None:
    import httpx

    verifier_holder = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        if "token" in request.url.path:
            assert "resource=https" in body or "resource=" in body
            assert "code_verifier=" in body
            return httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt",
                },
            )
        return httpx.Response(404)

    flow = _flow()
    pkce = generate_pkce_pair()
    verifier_holder["v"] = pkce.code_verifier
    from node_wire_runtime.mcp_client.oauth_flow import AuthorizationSession

    session = AuthorizationSession(
        state="s",
        pkce=pkce,
        redirect_uri="http://127.0.0.1:1/callback",
        resource="https://mcp.example.com/mcp",
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tokens = await flow.exchange_code("auth-code", session=session, http_client=client)

    assert tokens.access_token == "at"
    assert tokens.refresh_token == "rt"
