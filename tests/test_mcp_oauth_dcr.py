from __future__ import annotations

from pathlib import Path

import pytest
import httpx

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
from node_wire_runtime.mcp_client.dcr import resolve_client_registration, register_dynamic_client
from node_wire_runtime.mcp_client.storage import RegistrationStore


def _discovery(*, with_dcr: bool = True) -> DiscoveryResult:
    reg = "https://issuer.example/register" if with_dcr else None
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
            registration_endpoint=reg,
            scopes_supported=None,
            raw={},
        ),
        issuer="https://issuer.example",
    )


def _config(**kwargs) -> McpClientConfig:
    return McpClientConfig(
        server=McpServerConfig(url="https://mcp.example.com/mcp"),
        auth=AuthConfig(**kwargs),
    )


@pytest.mark.asyncio
async def test_dcr_registers_and_persists(tmp_path: Path) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/register"):
            captured["body"] = request.read().decode()
            return httpx.Response(
                201,
                json={"client_id": "dyn-client", "client_secret": None},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    cfg = _config(registration_store_path=str(tmp_path))
    store = RegistrationStore(tmp_path)
    async with httpx.AsyncClient(transport=transport) as client:
        reg = await resolve_client_registration(
            cfg,
            _discovery(),
            redirect_uris=["http://127.0.0.1:8765/callback"],
            store=store,
            http_client=client,
        )
        again = await resolve_client_registration(
            cfg,
            _discovery(),
            redirect_uris=["http://127.0.0.1:8765/callback"],
            store=store,
            http_client=client,
        )

    assert reg.client_id == "dyn-client"
    assert again.client_id == "dyn-client"
    assert "authorization_code" in captured.get("body", "")
    assert store.get("https://issuer.example") is not None


@pytest.mark.asyncio
async def test_configured_client_id_skips_dcr() -> None:
    cfg = _config(client=AuthClientConfig(id="static-id", secret=""))
    reg = await resolve_client_registration(
        cfg,
        _discovery(),
        redirect_uris=["http://127.0.0.1:1/callback"],
    )
    assert reg.client_id == "static-id"


@pytest.mark.asyncio
async def test_register_dynamic_client_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert body["token_endpoint_auth_method"] == "none"
        assert "authorization_code" in body["grant_types"]
        return httpx.Response(200, json={"client_id": "c1"})

    transport = httpx.MockTransport(handler)
    cfg = _config()
    async with httpx.AsyncClient(transport=transport) as client:
        reg = await register_dynamic_client(
            cfg,
            _discovery(),
            redirect_uris=["http://127.0.0.1:9/callback"],
            http_client=client,
        )
    assert reg.client_id == "c1"
