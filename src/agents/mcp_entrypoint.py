#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP Server — all connectors exposed via MCP. Usage: python -m agents.mcp_entrypoint"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

# Override inherited shell env so MCP auth/policy settings in project .env
# are applied predictably across local runs.
load_dotenv(override=True)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.mcp_entrypoint")


def main() -> None:
    from bindings.mcp_server.server import McpServer

    transport = os.getenv("NW_MCP_TRANSPORT", "stdio").strip().lower()
    logger.info(f"Starting Node Wire MCP server (transport={transport}, manifest-driven)")
    McpServer(server_name="node-wire").run(transport=transport)


if __name__ == "__main__":
    main()
