#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import os

import uvicorn
from dotenv import load_dotenv

from bindings.rest_api.app import app as rest_app
from bindings.mcp_server.server import McpServer
from node_wire_runtime.log_sanitization import install_sanitizing_log_filter
from node_wire_runtime.observability import init_observability

# Match ``bindings.rest_api.app``: honor ``NW_REST_LOAD_DOTENV`` and never override
# keys already set (pytest/conftest sets ``NW_REST_LOAD_DOTENV=false`` before imports).
if os.environ.get("NW_REST_LOAD_DOTENV", "true").lower() not in ("0", "false", "no"):
    load_dotenv(override=False)

logging.basicConfig(level=logging.INFO)
install_sanitizing_log_filter()
logger = logging.getLogger("bindings.entrypoint")
logging.getLogger("opentelemetry.exporter.otlp.proto.http").setLevel(logging.DEBUG)


def main() -> None:
    mode = os.getenv("MODE", "API").upper()
    logger.info("Starting Node Wire", extra={"mode": mode})

    # Initialize OpenTelemetry + OpenLLMetry/Traceloop once for the process.
    init_observability(app_name="node-wire")

    if mode == "API":
        port = int(os.getenv("PORT", "8000"))
        host = os.getenv("NW_REST_HOST", "127.0.0.1")
        uvicorn.run(rest_app, host=host, port=port)
    elif mode == "GRPC":
        # Import gRPC server lazily so API/MCP modes do not require
        # gRPC-specific dependencies or generated stubs at import time.
        from bindings.grpc_server.server import serve as grpc_serve

        grpc_serve(port=50051)
    elif mode == "MCP":
        # For the POC we just start a simple process that can be interacted
        # with manually or via a thin wrapper; a full JSON-RPC loop is out of scope.
        server = McpServer()
        logger.info(
            "MCP server ready (list_tools available)",
            extra={"tool_count": len(server.list_tools())},
        )
        import time

        while True:
            time.sleep(60)
    else:
        raise SystemExit(f"Unknown MODE {mode!r}. Expected one of: API, GRPC, MCP.")


if __name__ == "__main__":
    main()
