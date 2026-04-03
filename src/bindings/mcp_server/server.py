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

    async def _run_stdio_async(self) -> None:
        from mcp.server import NotificationOptions, Server as LowLevelServer
        from mcp.server.stdio import stdio_server
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


if __name__ == "__main__":
    # Simple demo runner that prints tool list and exits.
    server = McpServer()
    print(json.dumps(server.list_tools(), indent=2))
