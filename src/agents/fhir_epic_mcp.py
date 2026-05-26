#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP Server — Epic FHIR connector only. Usage: python -m agents.fhir_epic_mcp"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.fhir_epic_mcp")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    transport = os.getenv("NW_MCP_TRANSPORT", "stdio").strip().lower()
    logger.info(f"Starting nw-smartonfhir-epic MCP server (transport={transport}, manifest-driven)")
    McpServer(
        server_name="nw-smartonfhir-epic",
        connector_ids=["fhir_epic"],
    ).run(transport=transport)


if __name__ == "__main__":
    main()
