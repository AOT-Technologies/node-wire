#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Internal demo: MCP OAuth phases 0-4 with mock issuer (no real network)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx

from node_wire_runtime.mcp_client import (
    AuthorizationCodeFlow,
    DiscoveryCache,
    McpClientConfig,
    McpServerConfig,
    RegistrationStore,
    TokenManager,
    discover,
    resolve_client_registration,
)
from node_wire_runtime.mcp_client.redirect_listener import AuthorizationCallback
from node_wire_runtime.mcp_client.token_storage import InMemoryTokenStore

MCP_URL = "https://mcp.example.com/mcp"
ISSUER = "https://issuer.example"

PRM = {"resource": MCP_URL, "authorization_servers": [ISSUER]}
AS_META = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "registration_endpoint": f"{ISSUER}/register",
}


def mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "oauth-protected-resource" in path:
        return httpx.Response(200, json=PRM)
    if "oauth-authorization-server" in path:
        return httpx.Response(200, json=AS_META)
    if path.endswith("/register"):
        return httpx.Response(201, json={"client_id": "demo-mcp-client"})
    if path.endswith("/token"):
        form = request.read().decode()
        assert "resource=" in form
        return httpx.Response(
            200,
            json={
                "access_token": "demo-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "demo-refresh",
            },
        )
    return httpx.Response(404)


def section(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def main() -> None:
    section("1. Configuration")
    config = McpClientConfig(server=McpServerConfig(url=MCP_URL))
    print("  MCP server:", config.canonical_server_url)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        section("2. Discovery")
        discovery = await discover(config, cache=DiscoveryCache(3600), http_client=http)
        print("  Issuer:", discovery.issuer)

        section("3. DCR")
        with tempfile.TemporaryDirectory() as tmp:
            store = RegistrationStore(Path(tmp))
            reg = await resolve_client_registration(
                config,
                discovery,
                redirect_uris=["http://127.0.0.1:9999/callback"],
                store=store,
                http_client=http,
            )
            print("  client_id:", reg.client_id)

            section("4. Authorization (simulated)")
            flow = AuthorizationCodeFlow(config, discovery=discovery, registration=reg)
            session, url = await flow.prepare_authorization_session(
                redirect_uri="http://127.0.0.1:9999/callback"
            )
            print("  Authorize URL contains resource:", "resource=" in url)
            callback = AuthorizationCallback(
                code="demo-code",
                state=session.state,
                error=None,
                error_description=None,
            )
            code = flow.validate_callback(callback, expected_state=session.state)
            tokens = await flow.exchange_code(code, session=session, http_client=http)
            print("  access_token length:", len(tokens.access_token))

            section("5. Token manager")
            mgr = TokenManager(
                config,
                user_id="demo-user",
                token_store=InMemoryTokenStore(),
                discovery=discovery,
                registration=reg,
                auth_flow=flow,
            )
            mgr.persist_oauth_token_set(tokens, issuer=discovery.issuer)
            bearer = await mgr.get_bearer_token(http_client=http)
            print("  Bearer ready, length:", len(bearer))

    section("Done")


if __name__ == "__main__":
    asyncio.run(main())
