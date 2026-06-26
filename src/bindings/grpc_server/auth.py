"""
gRPC API authentication (enterprise default: required API key or JWT).

Environment:
  NW_GRPC_API_KEY     — shared secret; passed via metadata key 'authorization' or 'x-api-key'.
  NW_GRPC_API_KEY_SCOPES — scopes for the shared API key (JSON array or comma/space-separated).
  NW_GRPC_JWT_SECRET  — optional HS256 secret; if set, tokens with three segments are verified as JWTs.
  NW_GRPC_AUTH_DISABLED — if ``true``/``1``/``yes``, skip auth (local dev only; do not use in production).

After successful auth, normalized caller identity is stored in a context variable and forwarded
to ``connector.run`` for policy hooks (same shape as REST/MCP).
"""

from __future__ import annotations

import contextvars
import os
from typing import Any, Callable

import grpc

from node_wire_runtime.caller_identity import (
    CallerIdentity,
    verify_bearer_token_and_identity,
)

_grpc_caller_identity_ctx: contextvars.ContextVar[CallerIdentity | None] = contextvars.ContextVar(
    "grpc_caller_identity",
    default=None,
)


def get_grpc_caller_identity() -> CallerIdentity | None:
    """Return caller identity set by :class:`GrpcAuthInterceptor` for the current RPC."""
    return _grpc_caller_identity_ctx.get()


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _extract_token(metadata: tuple[tuple[str, str], ...]) -> str | None:
    for key, value in metadata:
        k = key.lower()
        if k == "authorization":
            if value.lower().startswith("bearer "):
                return value[7:].strip()
            return value.strip()
        if k == "x-api-key":
            return value.strip()
    return None


def verify_grpc_token_and_identity(
    token: str,
    *,
    api_key: str | None,
    jwt_secret: str | None,
) -> tuple[bool, CallerIdentity | None]:
    """Validate gRPC bearer/API-key token and build caller identity."""
    return verify_bearer_token_and_identity(
        token,
        api_key=api_key,
        jwt_secret=jwt_secret,
        api_key_scopes_env="NW_GRPC_API_KEY_SCOPES",
        api_key_auth_type="grpc_api_key",
    )


def _wrap_unary_handler(
    handler: grpc.RpcMethodHandler,
    identity: CallerIdentity,
) -> grpc.RpcMethodHandler:
    if handler.unary_unary is None:
        return handler

    original = handler.unary_unary

    def wrapped(request: Any, context: grpc.ServicerContext) -> Any:
        token = _grpc_caller_identity_ctx.set(identity)
        try:
            return original(request, context)
        finally:
            _grpc_caller_identity_ctx.reset(token)

    return grpc.unary_unary_rpc_method_handler(
        wrapped,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


class GrpcAuthInterceptor(grpc.ServerInterceptor):
    def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Any],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        if _truthy(os.environ.get("NW_GRPC_AUTH_DISABLED")):
            return continuation(handler_call_details)

        api_key = os.environ.get("NW_GRPC_API_KEY")
        jwt_secret = os.environ.get("NW_GRPC_JWT_SECRET")

        def _abort_with_status(code: grpc.StatusCode, details: str) -> Any:
            def abort(request: Any, context: grpc.ServicerContext) -> None:
                context.abort(code, details)

            return grpc.unary_unary_rpc_method_handler(abort)

        if not api_key and not jwt_secret:
            return _abort_with_status(
                grpc.StatusCode.UNAVAILABLE,
                "gRPC API authentication is not configured. Set NW_GRPC_API_KEY "
                "(and optionally NW_GRPC_JWT_SECRET), or set NW_GRPC_AUTH_DISABLED=true "
                "for local development only.",
            )

        token = _extract_token(handler_call_details.invocation_metadata or ())
        if not token:
            return _abort_with_status(grpc.StatusCode.UNAUTHENTICATED, "Authentication required")

        ok, identity = verify_grpc_token_and_identity(
            token,
            api_key=api_key,
            jwt_secret=jwt_secret,
        )
        if not ok or identity is None:
            return _abort_with_status(grpc.StatusCode.UNAUTHENTICATED, "Invalid API key or token")

        handler = continuation(handler_call_details)
        if handler is None:
            return None
        return _wrap_unary_handler(handler, identity)
