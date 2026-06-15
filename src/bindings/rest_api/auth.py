#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
REST API authentication (enterprise default: required API key or JWT).

Environment:
  NW_REST_API_KEY     — shared secret; send as ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``.
  NW_REST_JWT_SECRET  — optional HS256 secret; if set, Bearer tokens with three segments are verified as JWTs.
  NW_REST_AUTH_DISABLED — if ``true``/``1``/``yes``, skip auth (local dev only; do not use in production).

Public (unauthenticated): ``GET /health`` only. OpenAPI UI requires auth.

After successful auth, normalized caller identity (principal / tenant_id / scopes) is stored on
``request.state.nw_rest_caller_identity`` and forwarded to ``connector.run`` for policy hooks.
"""

from __future__ import annotations

import hashlib
import os
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from node_wire_runtime.caller_identity import (
    CallerIdentity,
    verify_bearer_token_and_identity,
)


REST_CALLER_STATE_KEY = "nw_rest_caller_identity"


def get_rest_caller_identity(request: Request) -> CallerIdentity | None:
    """Return JWT/API-key caller identity attached by middleware, if any."""
    return getattr(request.state, REST_CALLER_STATE_KEY, None)


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


def get_request_identity_key(request: Request) -> str:
    """
    Return a stable, non-sensitive identity key for request-level controls.

    Preference order:
    1) Auth token/API key (fingerprinted, never returned raw)
    2) X-Forwarded-For first hop
    3) request.client.host
    """
    token = _extract_bearer_or_api_key(request)
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"token:{digest}"
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", maxsplit=1)[0].strip()
    if forwarded:
        return f"ip:{forwarded}"
    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


def verify_rest_token_and_identity(
    token: str,
    *,
    api_key: str | None,
    jwt_secret: str | None,
) -> tuple[bool, CallerIdentity | None]:
    """
    Validate REST bearer/API-key token and build caller identity (same shape as MCP).

    Shared API key scopes come from ``NW_REST_API_KEY_SCOPES`` (JSON array or
    comma/space-separated). Empty means no scopes; use explicit ``*`` only when
    intended (JWT-style superuser for the policy hook).
    """
    if api_key and token == api_key:
        return verify_bearer_token_and_identity(
            token,
            api_key=api_key,
            jwt_secret=None,
            api_key_scopes_env="NW_REST_API_KEY_SCOPES",
            api_key_auth_type="rest_api_key",
        )

    if jwt_secret and token.count(".") == 2:
        return verify_bearer_token_and_identity(
            token,
            api_key=None,
            jwt_secret=jwt_secret,
            api_key_scopes_env="NW_REST_API_KEY_SCOPES",
            api_key_auth_type="rest_api_key",
        )

    return False, None


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

        ok, identity = verify_rest_token_and_identity(token, api_key=api_key, jwt_secret=jwt_secret)
        if not ok or identity is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "Invalid API key or token"},
                headers={"WWW-Authenticate": 'Bearer realm="node-wire"'},
            )

        setattr(request.state, REST_CALLER_STATE_KEY, identity)
        return await call_next(request)
