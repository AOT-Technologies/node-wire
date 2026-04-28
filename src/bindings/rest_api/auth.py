"""
REST API authentication (enterprise default: required API key or JWT).

Environment:
  NW_REST_API_KEY     — shared secret; send as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
  NW_REST_JWT_SECRET  — optional HS256 secret; if set, Bearer tokens with three segments are verified as JWTs.
  NW_REST_AUTH_DISABLED — if ``true``/``1``/``yes``, skip auth (local dev only; do not use in production).

Public (unauthenticated): ``GET /health`` only. OpenAPI UI requires auth.
"""
from __future__ import annotations

import os
from typing import Callable

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def _is_public_path(path: str) -> bool:
    p = path.rstrip("/") or "/"
    return p == "/health"


def _extract_bearer_or_api_key(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth:
        auth_val = auth.strip()
        if auth_val.lower().startswith("bearer "):
            return auth_val[7:].strip()
    x = request.headers.get("x-api-key")
    if x and x.strip():
        return x.strip()
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


class RestAuthMiddleware(BaseHTTPMiddleware):
    """Require API key or valid JWT for all routes except public paths."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        if _truthy(os.environ.get("NW_REST_AUTH_DISABLED")):
            return await call_next(request)

        api_key = os.environ.get("NW_REST_API_KEY")
        jwt_secret = os.environ.get("NW_REST_JWT_SECRET")

        if not api_key and not jwt_secret:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "REST API authentication is not configured. Set NW_REST_API_KEY "
                        "(and optionally NW_REST_JWT_SECRET), or set NW_REST_AUTH_DISABLED=true "
                        "for local development only."
                    )
                },
            )

        token = _extract_bearer_or_api_key(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
                headers={"WWW-Authenticate": 'Bearer realm="node-wire"'},
            )

        if not _verify_token(token, api_key=api_key, jwt_secret=jwt_secret):
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key or token"},
                headers={"WWW-Authenticate": 'Bearer realm="node-wire"'},
            )

        return await call_next(request)
