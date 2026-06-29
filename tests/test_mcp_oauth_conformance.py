#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Section 11 conformance checklist — unit/integration coverage with mock AS + MCP RS.
"""

from __future__ import annotations

import pytest
import httpx

from node_wire_runtime.mcp_client.challenges import parse_www_authenticate
from node_wire_runtime.mcp_client.config import McpClientConfig, McpServerConfig
from node_wire_runtime.mcp_client.discovery import discover, DiscoveryCache
from node_wire_runtime.mcp_client.dcr import resolve_client_registration
from node_wire_runtime.mcp_client.oauth_flow import AuthorizationCodeFlow
from node_wire_runtime.mcp_client.redirect_listener import AuthorizationCallback
from node_wire_runtime.mcp_client.storage import RegistrationStore


@pytest.mark.asyncio
async def test_conformance_discovery_and_dcr_and_pkce_flow(tmp_path) -> None:
    """Checklist: discovery, DCR, PKCE+resource, state validation, bearer usage."""
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
        path = request.url.path
        if path.endswith("/prm") or "oauth-protected-resource" in path:
            return httpx.Response(200, json=prm)
        if "oauth-authorization-server" in path:
            return httpx.Response(200, json=as_meta)
        if path.endswith("/register"):
            return httpx.Response(201, json={"client_id": "conf-client"})
        if path.endswith("/token"):
            body = request.read().decode()
            assert "resource=" in body
            assert "code_verifier=" in body
            return httpx.Response(
                200,
                json={
                    "access_token": "conf-access",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "conf-refresh",
                },
            )
        return httpx.Response(404)

    config = McpClientConfig(server=McpServerConfig(url="https://mcp.example.com/mcp"))
    config.auth.registration_store_path = str(tmp_path)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        discovery = await discover(
            config,
            www_authenticate='Bearer resource_metadata="https://issuer.example/prm"',
            cache=DiscoveryCache(3600),
            http_client=http,
        )
        reg = await resolve_client_registration(
            config,
            discovery,
            redirect_uris=["http://127.0.0.1:42/callback"],
            store=RegistrationStore(tmp_path),
            http_client=http,
        )
        flow = AuthorizationCodeFlow(config, discovery=discovery, registration=reg)
        session, url = await flow.prepare_authorization_session(
            redirect_uri="http://127.0.0.1:42/callback"
        )
        assert "code_challenge_method=S256" in url
        assert "resource=" in url
        assert len(session.state) >= 16
        callback = AuthorizationCallback(
            code="code-1",
            state=session.state,
            error=None,
            error_description=None,
        )
        code = flow.validate_callback(callback, expected_state=session.state)
        tokens = await flow.exchange_code(code, session=session, http_client=http)

    assert tokens.access_token == "conf-access"
    assert reg.client_id == "conf-client"
    challenge = parse_www_authenticate('Bearer error="invalid_token"')
    assert challenge is not None
    assert challenge.treat_as_unauthorized
