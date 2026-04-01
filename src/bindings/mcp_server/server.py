from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from bindings.factory import ConnectorFactory
from connectors import auto_register
from connectors.manifest import build_manifest
from runtime import SDKConnector

logger = logging.getLogger("bindings.mcp_server")


class McpServer:
    """
    Minimal MCP-style server abstraction for the POC.

    This does not implement the full Model Context Protocol over JSON-RPC,
    but exposes two conceptual operations:
      - list_tools(): returns connector/actions manifest
      - invoke_tool(name, arguments): executes the corresponding connector
    """

    def __init__(self) -> None:
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()

    def list_tools(self) -> List[Dict[str, Any]]:
        connectors = self._factory.list_for_protocol("mcp")
        manifest = build_manifest(connectors)
        tools: List[Dict[str, Any]] = []
        for entry in manifest:
            tools.append(
                {
                    "name": f"{entry['connector_id']}.{entry['action']}",
                    "description": f"{entry['connector_id']} {entry['action']} connector action",
                    "input_schema": entry["input_schema"],
                }
            )
        return tools

    async def invoke_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            connector_id, action = name.split(".", 1)
        except ValueError:
            raise ValueError("Tool name must be in the form '<connector>.<action>'")

        connector = self._factory.get_for_protocol(connector_id, "mcp")
        if connector is None:
            raise ValueError(f"Connector {connector_id!r} is not available via MCP.")

        run_args = dict(arguments)
        if isinstance(connector, SDKConnector):
            run_args.setdefault("action", action)

        response = await connector.run(run_args)
        return response.model_dump()


if __name__ == "__main__":
    # Simple demo runner that prints tool list and exits.
    server = McpServer()
    print(json.dumps(server.list_tools(), indent=2))

