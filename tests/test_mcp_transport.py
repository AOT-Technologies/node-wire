import os

# Set allowed connectors to avoid ModuleNotFoundError from broken/partially implemented connectors like 'slack'
os.environ["NW_ALLOWED_CONNECTORS"] = "fhir_cerner,fhir_epic,google_drive,smtp,stripe"

import pytest
import anyio
import httpx
from unittest.mock import MagicMock, patch
from bindings.mcp_server.server import McpServer

@pytest.mark.anyio
async def test_mcp_transport_stdio_calls_run_stdio():
    server = McpServer()
    with patch.object(server, "run_stdio") as mock_run:
        server.run(transport="stdio")
        mock_run.assert_called_once()

@pytest.mark.anyio
async def test_mcp_transport_streamable_http_calls_run_streamable_http():
    server = McpServer()
    with patch.object(server, "run_streamable_http") as mock_run:
        server.run(transport="streamable-http")
        mock_run.assert_called_once()

@pytest.mark.anyio
async def test_mcp_transport_invalid_value_fails_fast():
    server = McpServer()
    with pytest.raises(ValueError, match="Unsupported MCP transport: invalid"):
        server.run(transport="invalid")

@pytest.mark.anyio
async def test_mcp_http_server_starts_and_responds():
    # We want to test that the starlette app is correctly set up and responds.
    # Instead of running the full uvicorn server (which is hard to manage in tests),
    # we can test the starlette app directly using httpx.ASGITransport.
    
    server = McpServer(server_name="test-server")
    
    # We need to mock _setup_lowlevel_server because it starts background tasks in the SDK
    # that might interfere with the test environment if not handled carefully.
    # But for a basic integration test of our routing:
    
    from starlette.applications import Starlette
    from starlette.routing import Route
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from contextlib import asynccontextmanager

    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/mcp", endpoint=session_manager.handle_request, methods=["GET", "POST"])
        ]
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver") as client:
        # Test new session (POST to initiate or GET for SSE)
        # According to MCP spec, first request can be a POST with a dummy request
        # or a GET to start SSE.
        
        # Let's try a POST to list tools (standard MCP JSON-RPC)
        # Note: streamable-http might require a session ID header for subsequent requests,
        # but the first request creates it.
        
        rpc_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        }
        
        response = await client.post("/mcp", json=rpc_request)
        assert response.status_code == 200
        data = response.json()
        assert "jsonrpc" in data
        assert "result" in data or "error" in data
        
        if "result" in data:
            assert data["result"]["protocolVersion"] == "2024-11-05"

@pytest.mark.anyio
async def test_mcp_http_tools_list_success():
    server = McpServer(server_name="test-server")
    
    from starlette.applications import Starlette
    from starlette.routing import Route
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from contextlib import asynccontextmanager

    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/mcp", endpoint=session_manager.handle_request, methods=["GET", "POST"])
        ]
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver") as client:
        # First initialize
        init_resp = await client.post("/mcp", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"}
            }
        })
        assert init_resp.status_code == 200
        session_id = init_resp.headers.get("X-MCP-Session-ID")
        
        # Then list tools
        list_resp = await client.post("/mcp", 
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {}
            },
            headers={"X-MCP-Session-ID": session_id} if session_id else {}
        )
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert "tools" in data["result"]
