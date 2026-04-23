from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Mapping, Optional

from bindings.factory import ConnectorFactory
from bindings.mcp_server.auth import (
    McpAuthError,
    McpIdentity,
    authenticate_mcp_request,
)
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION, build_manifest
from node_wire_runtime import BaseConnector, ConnectorResponse, ErrorCategory
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

    def list_tools(self, *, identity: McpIdentity | None = None) -> List[Dict[str, Any]]:
        self._ensure_identity(identity=identity)
        return self._list_tools_impl()

    def _list_tools_impl(self) -> List[Dict[str, Any]]:
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

    def _ensure_identity(
        self,
        *,
        identity: McpIdentity | None,
        meta: Mapping[str, Any] | None = None,
    ) -> McpIdentity | None:
        if identity is not None:
            return identity
        return authenticate_mcp_request(meta=meta)

    def _request_meta_from_context(self) -> Mapping[str, Any] | None:
        try:
            from mcp.server.lowlevel.server import request_ctx

            ctx = request_ctx.get()
        except Exception:
            return None
        if ctx is None or ctx.meta is None:
            return None
        if hasattr(ctx.meta, "model_dump"):
            dumped = ctx.meta.model_dump()  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dumped
            return None
        if isinstance(ctx.meta, dict):
            return ctx.meta
        return None

    async def invoke_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        identity: McpIdentity | None = None,
    ) -> Dict[str, Any]:
        identity = self._ensure_identity(identity=identity)
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

        response = await connector.run(
            run_args,
            principal=identity.principal if identity else None,
            tenant_id=identity.tenant_id if identity else None,
            scopes=identity.scopes if identity else None,
        )
        return response.model_dump()

    def _setup_lowlevel_server(self) -> Any:
        from mcp.server import NotificationOptions, Server as LowLevelServer
        from mcp.types import Tool

        low = LowLevelServer(self._server_name)

        @low.list_tools()
        async def handle_list_tools() -> list[Tool]:
            meta = self._request_meta_from_context()
            try:
                identity = self._ensure_identity(identity=None, meta=meta)
            except McpAuthError as exc:
                logger.warning(
                    "MCP tools/list denied by authentication",
                    extra={
                        "status_code": exc.status_code,
                        "error_code": exc.error_code,
                    },
                )
                raise RuntimeError(json.dumps(exc.to_payload())) from exc
            if identity:
                logger.info(
                    "MCP tools/list authorized",
                    extra={
                        "principal": identity.principal,
                        "tenant_id": identity.tenant_id or "",
                        "auth_type": identity.auth_type,
                    },
                )
            out: list[Tool] = []
            for t in self._list_tools_impl():
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
            meta = self._request_meta_from_context()
            try:
                identity = self._ensure_identity(identity=None, meta=meta)
            except McpAuthError as exc:
                logger.warning(
                    "MCP tools/call denied by authentication",
                    extra={
                        "tool_name": tool_name,
                        "status_code": exc.status_code,
                        "error_code": exc.error_code,
                    },
                )
                return ConnectorResponse(
                    success=False,
                    data=None,
                    error_code=exc.error_code,
                    error_category=ErrorCategory.AUTH,
                    message=exc.detail,
                    trace_id=f"mcp-auth-{uuid.uuid4()}",
                    details=exc.to_payload(),
                ).model_dump()

            if identity:
                logger.info(
                    "MCP tools/call authorized",
                    extra={
                        "tool_name": tool_name,
                        "principal": identity.principal,
                        "tenant_id": identity.tenant_id or "",
                        "auth_type": identity.auth_type,
                    },
                )
            return await self.invoke_tool(tool_name, arguments or {}, identity=identity)

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
