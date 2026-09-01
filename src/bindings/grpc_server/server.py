#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json
import logging
import os
from concurrent import futures
from typing import Any

import grpc
from grpc_health.v1 import health as grpc_health
from grpc_health.v1 import health_pb2, health_pb2_grpc

from bindings.factory import ConnectorFactory
from bindings.invoke import ConnectorNotExposed, invoke
from node_wire_runtime.connector_registry import auto_register
from node_wire_runtime import ConnectorResponse, ErrorCategory
from node_wire_runtime.config_store import ConfigNotFoundError
from node_wire_runtime.identity import (
    MissingTenantError,
    resolve_config_name,
    resolve_tenant_id,
)
from node_wire_runtime.rate_limit import global_rate_limiter, RateLimitExceeded

from .async_runner import BackgroundAsyncRunner
from . import connector_pb2, connector_pb2_grpc  # type: ignore[attr-defined]
from .auth import GrpcAuthInterceptor, get_grpc_caller_identity
from .tls_config import configure_grpc_server_port, resolve_grpc_host

logger = logging.getLogger("bindings.grpc_server")

_async_runner = BackgroundAsyncRunner()


class ConnectorServiceServicer(connector_pb2_grpc.ConnectorServiceServicer):
    def __init__(self, factory: ConnectorFactory | None = None) -> None:
        auto_register()
        if factory is not None:
            self._factory = factory
        else:
            self._factory = ConnectorFactory()
            self._factory.load()
            # Same YAML hydrate as REST/MCP; skip when factory is injected (tests).
            from node_wire_runtime.tenant_persistence import load_tenants

            load_tenants(self._factory.store)

    async def _invoke_async(
        self,
        request: connector_pb2.InvokeRequest,  # type: ignore[name-defined, attr-defined]
        metadata: dict[str, str] | None = None,
    ) -> connector_pb2.InvokeResponse:  # type: ignore[name-defined, attr-defined]
        try:
            # Skip rate limiting if disabled (same env var as REST/MCP).
            if os.environ.get("NW_RATE_LIMIT_DISABLED", "false").lower() not in (
                "true",
                "1",
                "yes",
            ):
                await global_rate_limiter.acquire()
        except RateLimitExceeded as e:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                success=False,
                error_code="RATE_LIMIT_EXCEEDED",
                error_category=ErrorCategory.RETRYABLE.value,
                message=str(e),
                trace_id="",
            )

        identity = get_grpc_caller_identity()
        try:
            tenant_id = resolve_tenant_id(headers=metadata or {}, jwt_identity=identity)
        except MissingTenantError as exc:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                success=False,
                error_code="MISSING_TENANT",
                error_category=ErrorCategory.AUTH.value,
                message=str(exc),
                trace_id="",
            )

        if not self._factory.is_exposed(request.connector_id, "grpc"):
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
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
                return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                    success=False,
                    error_code="INVALID_JSON",
                    error_category=ErrorCategory.BUSINESS.value,
                    message=f"Failed to parse payload_json: {e}",
                    trace_id="",
                )

        if not isinstance(payload, dict):
            payload = {}

        try:
            response: ConnectorResponse = await invoke(
                self._factory,
                connector_id=request.connector_id,
                action=request.action,
                payload=payload,
                protocol="grpc",
                tenant_id=tenant_id,
                config_name=resolve_config_name(request.config_name or None),
                principal=identity.principal if identity else None,
                scopes=identity.scopes if identity else None,
            )
        except ConnectorNotExposed:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                success=False,
                error_code="CONNECTOR_NOT_AVAILABLE",
                error_category=ErrorCategory.BUSINESS.value,
                message=f"Connector {request.connector_id!r} is not available via gRPC.",
                trace_id="",
            )
        except ConfigNotFoundError:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                success=False,
                error_code="CONFIG_NOT_FOUND",
                error_category=ErrorCategory.AUTH.value,
                message="No connector configuration for this tenant",
                trace_id="",
            )
        except ValueError as exc:
            return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
                success=False,
                error_code="INVALID_PAYLOAD",
                error_category=ErrorCategory.BUSINESS.value,
                message=str(exc),
                trace_id="",
            )

        data_json = json.dumps(response.data) if response.data is not None else ""
        error_category = (
            response.error_category.value if response.error_category is not None else ""
        )

        return connector_pb2.InvokeResponse(  # type: ignore[name-defined, attr-defined]
            success=response.success,
            data_json=data_json,
            error_code=response.error_code or "",
            error_category=error_category,
            message=response.message or "",
            trace_id=response.trace_id,
        )

    def Invoke(self, request, context):  # type: ignore[override]
        # gRPC metadata keys are lowercase by spec; tenant header lookup is
        # case-insensitive. Pinned once per unary call.
        metadata = {k.lower(): v for k, v in (context.invocation_metadata() or ())}
        return _async_runner.run(self._invoke_async(request, metadata))


def serve(port: int = 50051) -> None:
    _async_runner.start()
    interceptor = GrpcAuthInterceptor()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10), interceptors=(interceptor,))
    connector_pb2_grpc.add_ConnectorServiceServicer_to_server(ConnectorServiceServicer(), server)  # type: ignore[attr-defined]

    cert_path = os.environ.get("NW_GRPC_TLS_CERT_PATH")
    key_path = os.environ.get("NW_GRPC_TLS_KEY_PATH")
    host = resolve_grpc_host()
    configure_grpc_server_port(server, port=port, host=host, cert_path=cert_path, key_path=key_path)

    health_servicer = grpc_health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set("aot.connectors.ConnectorService", health_pb2.HealthCheckResponse.SERVING)

    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
