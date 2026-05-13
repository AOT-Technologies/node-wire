import pytest
import httpx
from unittest.mock import patch
from bindings.mcp_server.server import McpServer, _http_request_headers
from starlette.applications import Starlette
from starlette.routing import Route
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager


class _ASGIApp:
    def __init__(self, handler):
        self.handler = handler

    async def __call__(self, scope, receive, send):
        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        token = _http_request_headers.set(headers)
        try:
            await self.handler(scope, receive, send)
        finally:
            _http_request_headers.reset(token)


@pytest.fixture(autouse=True)
def allow_only_standard_connectors(monkeypatch):
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS", "fhir_cerner,fhir_epic,google_drive,smtp,stripe,http_generic"
    )


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
    server = McpServer(server_name="test-server")
    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    starlette_app = Starlette(
        routes=[
            Route(
                "/mcp", endpoint=_ASGIApp(session_manager.handle_request), methods=["GET", "POST"]
            )
        ]
    )

    async with session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver"
        ) as client:
            rpc_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            }
            response = await client.post(
                "/mcp", json=rpc_request, headers={"Accept": "application/json, text/event-stream"}
            )
            assert response.status_code == 200
            data = response.json()
            assert "jsonrpc" in data
            assert "result" in data or "error" in data
            if "result" in data:
                assert data["result"]["protocolVersion"] == "2024-11-05"


@pytest.mark.anyio
async def test_mcp_http_tools_list_success():
    server = McpServer(server_name="test-server")
    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    starlette_app = Starlette(
        routes=[
            Route(
                "/mcp", endpoint=_ASGIApp(session_manager.handle_request), methods=["GET", "POST"]
            )
        ]
    )

    common_headers = {"Accept": "application/json, text/event-stream"}

    async with session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver"
        ) as client:
            # First initialize
            init_resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                },
                headers=common_headers,
            )
            assert init_resp.status_code == 200
            # Use correct header name Mcp-Session-Id
            session_id = init_resp.headers.get("Mcp-Session-Id")

            # Then list tools
            headers = common_headers.copy()
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            list_resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert "tools" in data["result"]


@pytest.mark.anyio
async def test_mcp_http_tools_list_accepts_authorization_header(monkeypatch):
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(server_name="test-server", connector_ids=["smtp"])
    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    starlette_app = Starlette(
        routes=[
            Route(
                "/mcp", endpoint=_ASGIApp(session_manager.handle_request), methods=["GET", "POST"]
            )
        ]
    )

    common_headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer unit-test-secret",
    }

    async with session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver"
        ) as client:
            init_resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                },
                headers=common_headers,
            )
            assert init_resp.status_code == 200
            session_id = init_resp.headers.get("Mcp-Session-Id")

            headers = common_headers.copy()
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            list_resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert "tools" in data["result"]
            assert any(t["name"] == "smtp.send_email" for t in data["result"]["tools"])


@pytest.mark.anyio
async def test_mcp_http_tools_list_accepts_x_api_key_header(monkeypatch):
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(server_name="test-server", connector_ids=["smtp"])
    low = server._setup_lowlevel_server()
    session_manager = StreamableHTTPSessionManager(low, json_response=True)

    starlette_app = Starlette(
        routes=[
            Route(
                "/mcp", endpoint=_ASGIApp(session_manager.handle_request), methods=["GET", "POST"]
            )
        ]
    )

    common_headers = {
        "Accept": "application/json, text/event-stream",
        "X-API-Key": "unit-test-secret",
    }

    async with session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=starlette_app), base_url="http://testserver"
        ) as client:
            init_resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                },
                headers=common_headers,
            )
            assert init_resp.status_code == 200
            session_id = init_resp.headers.get("Mcp-Session-Id")

            headers = common_headers.copy()
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            list_resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert "tools" in data["result"]
            assert any(t["name"] == "smtp.send_email" for t in data["result"]["tools"])
