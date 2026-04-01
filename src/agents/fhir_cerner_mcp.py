"""MCP Server — Cerner FHIR connector only. Usage: python -m agents.fhir_cerner_mcp"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.fhir_cerner_mcp")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    logger.info("Starting nw-smartonfhir-cerner MCP server (stdio, manifest-driven)")
    McpServer(
        server_name="nw-smartonfhir-cerner",
        connector_ids=["fhir_cerner"],
    ).run_stdio()


if __name__ == "__main__":
    main()
