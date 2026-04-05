"""MCP Server — all connectors exposed via MCP. Usage: python -m agents.mcp_entrypoint"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.mcp_entrypoint")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    logger.info("Starting Node Wire MCP server (stdio, manifest-driven)")
    McpServer(server_name="node-wire").run_stdio()


if __name__ == "__main__":
    main()
