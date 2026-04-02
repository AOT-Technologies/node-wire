from __future__ import annotations

import asyncio
import json
import logging
from concurrent import futures
from typing import Any

import grpc

from bindings.factory import ConnectorFactory
from connectors import auto_register
from runtime import ConnectorResponse, ErrorCategory
from runtime.ingress import normalize_mcp_tool_arguments

from . import connector_pb2, connector_pb2_grpc  # type: ignore[attr-defined]

logger = logging.getLogger("bindings.grpc_server")


class ConnectorServiceServicer(connector_pb2_grpc.ConnectorServiceServicer):
    def __init__(self) -> None:
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()

    async def _invoke_async(self, request: connector_pb2.InvokeRequest) -> connector_pb2.InvokeResponse:  # type: ignore[name-defined]
        connector = self._factory.get_for_protocol(request.connector_id, "grpc")
        if connector is None:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined]
                success=False,
                error_code="CONNECTOR_NOT_AVAILABLE",
                error_category=ErrorCategory.BUSINESS.value,
                message=f"Connector {request.connector_id!r} is not available via gRPC.",
                trace_id="",
            )

        payload: Any = {}
        if request.payload_json:
            payload = json.loads(request.payload_json)

        if isinstance(payload, dict) and payload.get("action"):
            normalize_mcp_tool_arguments(connector, str(payload["action"]), payload)

        response: ConnectorResponse = await connector.run(payload)

        data_json = json.dumps(response.data) if response.data is not None else ""
        error_category = response.error_category.value if response.error_category is not None else ""

        return connector_pb2.InvokeResponse(  # type: ignore[name-defined]
            success=response.success,
            data_json=data_json,
            error_code=response.error_code or "",
            error_category=error_category,
            message=response.message or "",
            trace_id=response.trace_id,
        )

    def Invoke(self, request, context):  # type: ignore[override]
        # Bridge sync gRPC handler to async execution.
        return asyncio.run(self._invoke_async(request))


def serve(port: int = 50051) -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    connector_pb2_grpc.add_ConnectorServiceServicer_to_server(ConnectorServiceServicer(), server)  # type: ignore[attr-defined]
    server.add_insecure_port(f"[::]:{port}")
    logger.info("Starting gRPC server", extra={"port": port})
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()

