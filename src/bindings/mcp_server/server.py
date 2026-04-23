from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from bindings.factory import ConnectorFactory
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION, build_manifest
from node_wire_runtime import BaseConnector
from node_wire_runtime.ingress import enforce_authoritative_action, normalize_mcp_tool_arguments

logger = logging.getLogger("bindings.mcp_server")


class McpServer:
    """
    Manifest-driven MCP server: tools come from connector metadata; execution
    dispatches through ConnectorFactory and connector.run().

    Use list_tools() / invoke_tool() for programmatic access, or run_stdio()
    for a full MCP stdio transport.
    """

    def __init__(
        self,
        *,
        server_name: str = "node-wire",
        connector_ids: Optional[List[str]] = None,
    ) -> None:
        self._server_name = server_name
        self._connector_ids: Optional[frozenset[str]] = (
            None if connector_ids is None else frozenset(connector_ids)
        )
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()
        try:
            from importlib.metadata import version as pkg_version

            _pkg_ver = pkg_version("node-wire")
        except Exception:  # pragma: no cover
            _pkg_ver = "unknown"
        logger.info(
            "MCP server initialized | server_name=%s | manifest_contract=%s | package=%s",
            server_name,
            MCP_MANIFEST_CONTRACT_VERSION,
            _pkg_ver,
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        connectors = self._factory.list_for_protocol("mcp")
        manifest = build_manifest(connectors)
        tools: List[Dict[str, Any]] = []
        for entry in manifest:
            cid = entry["connector_id"]
            if self._connector_ids is not None and cid not in self._connector_ids:
                continue
            schema_desc = entry["input_schema"].get("description", "")
            tool_desc = (
                f"{schema_desc}\n" if schema_desc else ""
            ) + (
                f"Pass fields from inputSchema only; do not include an action field "
                f"(it is injected from the tool name). "
                f"Manifest contract v{MCP_MANIFEST_CONTRACT_VERSION}."
            )
            tools.append(
                {
                    "name": f"{cid}.{entry['action']}",
                    "description": tool_desc,
                    "input_schema": entry["input_schema"],
                    "output_schema": entry["output_schema"],
                }
            )
        return tools

    async def invoke_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            connector_id, action = name.split(".", 1)
        except ValueError:
            raise ValueError("Tool name must be in the form '<connector>.<action>'")

        if self._connector_ids is not None and connector_id not in self._connector_ids:
            raise ValueError(
                f"Connector {connector_id!r} is not allowed on this MCP server."
            )

        connector = self._factory.get_for_protocol(connector_id, "mcp")
        if connector is None:
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")

        run_args = normalize_mcp_tool_arguments(connector, action, arguments)
        enforce_authoritative_action(run_args, action)
        run_args["action"] = action

        response = await connector.run(run_args)
        return response.model_dump()

    def _setup_lowlevel_server(self) -> Any:
        from mcp.server import NotificationOptions, Server as LowLevelServer
        from mcp.types import Tool

        low = LowLevelServer(self._server_name)

        @low.list_tools()
        async def handle_list_tools() -> list[Tool]:
            out: list[Tool] = []
            for t in self.list_tools():
                kwargs: Dict[str, Any] = {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["input_schema"],
                    "outputSchema": t["output_schema"],
                }
                out.append(Tool(**kwargs))
            return out

        @low.call_tool()
        async def handle_call_tool(tool_name: str, arguments: dict) -> dict:
            return await self.invoke_tool(tool_name, arguments or {})

        return low

    async def _run_stdio_async(self) -> None:
        from mcp.server.stdio import stdio_server
        from mcp.server import NotificationOptions

        low = self._setup_lowlevel_server()

        async with stdio_server() as (read_stream, write_stream):
            await low.run(
                read_stream,
                write_stream,
                low.create_initialization_options(
                    notification_options=NotificationOptions()
                ),
            )

    def run_stdio(self) -> None:
        import anyio

        anyio.run(self._run_stdio_async)

    async def _run_streamable_http_async(self) -> None:
        import os
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        import uvicorn
        from contextlib import asynccontextmanager

        host = os.getenv("NW_MCP_HOST", "0.0.0.0")
        port = int(os.getenv("NW_MCP_PORT", "8081"))
        path = os.getenv("NW_MCP_PATH", "/mcp")

        low = self._setup_lowlevel_server()
        session_manager = StreamableHTTPSessionManager(low, json_response=True)

        @asynccontextmanager
        async def lifespan(app: Starlette):
            async with session_manager.run():
                yield

        # Use a wrapper class to ensure Starlette treats this as an ASGI app
        # without the automatic redirection logic of Mount().
        class _ASGIApp:
            def __init__(self, handler):
                self.handler = handler

            async def __call__(self, scope, receive, send):
                await self.handler(scope, receive, send)

        starlette_app = Starlette(
            lifespan=lifespan,
            routes=[
                Route(
                    path,
                    endpoint=_ASGIApp(session_manager.handle_request),
                    methods=["GET", "POST"],
                )
            ],
        )

        logger.info(f"Starting MCP streamable-http server on {host}:{port}{path}")
        config = uvicorn.Config(starlette_app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

    def run_streamable_http(self) -> None:
        import anyio

        anyio.run(self._run_streamable_http_async)

    def run(self, transport: str = "stdio") -> None:
        transport = transport.strip().lower()
        if transport == "stdio":
            self.run_stdio()
        elif transport == "streamable-http":
            self.run_streamable_http()
        else:
            raise ValueError(f"Unsupported MCP transport: {transport}")


if __name__ == "__main__":
    # Simple demo runner that prints tool list and exits.
    server = McpServer()
    print(json.dumps(server.list_tools(), indent=2))
