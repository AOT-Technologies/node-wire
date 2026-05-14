#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import json
import logging
import os
from concurrent import futures
from typing import Any

import grpc

from bindings.factory import ConnectorFactory
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.ingress import normalize_mcp_tool_arguments
from node_wire_runtime.rate_limit import global_rate_limiter, RateLimitExceeded

from . import connector_pb2, connector_pb2_grpc  # type: ignore[attr-defined]
from .auth import GrpcAuthInterceptor

logger = logging.getLogger("bindings.grpc_server")


class ConnectorServiceServicer(connector_pb2_grpc.ConnectorServiceServicer):
    def __init__(self) -> None:
        auto_register()
        self._factory = ConnectorFactory()
        self._factory.load()

    async def _invoke_async(
        self, request: connector_pb2.InvokeRequest
    ) -> connector_pb2.InvokeResponse:  # type: ignore[name-defined]
        try:
            await global_rate_limiter.acquire()
        except RateLimitExceeded as e:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined]
                success=False,
                error_code="RATE_LIMIT_EXCEEDED",
                error_category=ErrorCategory.RETRYABLE.value,
                message=str(e),
                trace_id="",
            )

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
            try:
                payload = json.loads(request.payload_json)
            except json.JSONDecodeError as e:
                return connector_pb2.InvokeResponse(  # type: ignore[name-defined]
                    success=False,
                    error_code="INVALID_JSON",
                    error_category=ErrorCategory.BUSINESS.value,
                    message=f"Failed to parse payload_json: {e}",
                    trace_id="",
                )

        if isinstance(payload, dict):
            # The payload MUST include the action for Pydantic discriminated union validation to succeed
            if request.action:
                payload["action"] = request.action

            if payload.get("action"):
                normalize_mcp_tool_arguments(connector, str(payload["action"]), payload)

        response: ConnectorResponse = await connector.run(payload)

        data_json = json.dumps(response.data) if response.data is not None else ""
        error_category = (
            response.error_category.value if response.error_category is not None else ""
        )

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
    interceptor = GrpcAuthInterceptor()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=(interceptor,))
    connector_pb2_grpc.add_ConnectorServiceServicer_to_server(ConnectorServiceServicer(), server)  # type: ignore[attr-defined]

    cert_path = os.environ.get("NW_GRPC_TLS_CERT_PATH")
    key_path = os.environ.get("NW_GRPC_TLS_KEY_PATH")

    if cert_path and key_path:
        # Load the TLS certificate and key
        with open(key_path, "rb") as f:
            private_key = f.read()
        with open(cert_path, "rb") as f:
            certificate_chain = f.read()

        server_credentials = grpc.ssl_server_credentials(((private_key, certificate_chain),))
        server.add_secure_port(f"[::]:{port}", server_credentials)
        logger.info("Starting secure gRPC server (TLS enabled)", extra={"port": port})
    else:
        server.add_insecure_port(f"[::]:{port}")
        logger.warning(
            "Starting insecure gRPC server (no TLS credentials found). "
            "Traffic will be unencrypted.",
            extra={"port": port},
        )

    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
