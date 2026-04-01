"""MCP Server — Google Drive connector only. Usage: python -m agents.google_drive_mcp"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.google_drive_mcp")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    logger.info("Starting nw-google-drive MCP server (stdio, manifest-driven)")
    McpServer(
        server_name="nw-google-drive",
        connector_ids=["google_drive"],
    ).run_stdio()


if __name__ == "__main__":
    main()
