from __future__ import annotations

import logging
import os

import uvicorn

from bindings.rest_api.app import app as rest_app
from bindings.mcp_server.server import McpServer
from node_wire_runtime.observability import init_observability

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bindings.entrypoint")
logging.getLogger("opentelemetry.exporter.otlp.proto.http").setLevel(logging.DEBUG)


def main() -> None:
    mode = os.getenv("MODE", "API").upper()
    logger.info("Starting Node Wire", extra={"mode": mode})

    # Initialize OpenTelemetry + OpenLLMetry/Traceloop once for the process.
    init_observability(app_name="node-wire")

    if mode == "API":
        port = int(os.getenv("PORT", "8000"))
        uvicorn.run(rest_app, host="0.0.0.0", port=port)
    elif mode == "GRPC":
        # Import gRPC server lazily so API/MCP modes do not require
        # gRPC-specific dependencies or generated stubs at import time.
        from bindings.grpc_server.server import serve as grpc_serve

        grpc_serve(port=50051)
    elif mode == "MCP":
        # For the POC we just start a simple process that can be interacted
        # with manually or via a thin wrapper; a full JSON-RPC loop is out of scope.
        server = McpServer()
        logger.info("MCP server ready (list_tools available)", extra={"tool_count": len(server.list_tools())})
        import time

        while True:
            time.sleep(60)
    else:
        raise SystemExit(f"Unknown MODE {mode!r}. Expected one of: API, GRPC, MCP.")


if __name__ == "__main__":
    main()

