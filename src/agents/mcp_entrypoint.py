"""MCP Server — all connectors exposed via MCP. Usage: python -m agents.mcp_entrypoint"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.mcp_entrypoint")
logging.getLogger("opentelemetry.exporter.otlp.proto.http").setLevel(logging.DEBUG)


def main() -> None:
    from node_wire_runtime.observability import init_observability

    init_observability(app_name="node-wire")

    from bindings.mcp_server.server import McpServer

    logger.info("Starting Node Wire MCP server (stdio, manifest-driven)")
    McpServer(server_name="node-wire").run_stdio()


if __name__ == "__main__":
    main()
