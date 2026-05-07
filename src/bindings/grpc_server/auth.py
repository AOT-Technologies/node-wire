"""
gRPC API authentication (enterprise default: required API key or JWT).

Environment:
  NW_GRPC_API_KEY     — shared secret; passed via metadata key 'authorization' or 'x-api-key'.
  NW_GRPC_JWT_SECRET  — optional HS256 secret; if set, tokens with three segments are verified as JWTs.
  NW_GRPC_AUTH_DISABLED — if ``true``/``1``/``yes``, skip auth (local dev only; do not use in production).
"""

from __future__ import annotations

import os
from typing import Any, Callable

import grpc
import jwt


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


def _verify_token(token: str, *, api_key: str | None, jwt_secret: str | None) -> bool:
    if api_key and token == api_key:
        return True
    if jwt_secret and token.count(".") == 2:
        try:
            jwt.decode(token, jwt_secret, algorithms=["HS256"])
            return True
        except jwt.PyJWTError:
            return False
    return False


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

        if not _verify_token(token, api_key=api_key, jwt_secret=jwt_secret):
            return _abort_with_status(grpc.StatusCode.UNAUTHENTICATED, "Invalid API key or token")

        return continuation(handler_call_details)
