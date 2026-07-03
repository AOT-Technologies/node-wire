#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import contextvars
import os
import logging
from pathlib import Path
from typing import Any, Mapping

import jwt
from dotenv import load_dotenv

from node_wire_runtime.caller_identity import (
    CallerIdentity,
    api_key_matches,
    build_caller_identity,
    decode_binding_jwt,
    parse_api_key_scopes_from_env,
)
from node_wire_runtime.auth.base import reset_upstream_bearer, set_upstream_bearer

logger = logging.getLogger(__name__)

_upstream_reset_ctx: contextvars.ContextVar[contextvars.Token | None] = contextvars.ContextVar(
    "_mcp_upstream_reset", default=None
)

# Back-compat: callers may still import ``McpIdentity`` / ``build_identity`` from MCP auth.
McpIdentity = CallerIdentity


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


class McpAuthError(PermissionError):
    def __init__(
        self,
        detail: str,
        *,
        status_code: int,
        error_code: str,
        www_authenticate: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.www_authenticate = www_authenticate

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detail": self.detail,
            "error_code": self.error_code,
            "status_code": self.status_code,
        }
        if self.www_authenticate:
            payload["www_authenticate"] = self.www_authenticate
        return payload


class McpAuthRequiredError(McpAuthError):
    def __init__(self) -> None:
        super().__init__(
            "Authentication required",
            status_code=401,
            error_code="MCP_AUTH_REQUIRED",
            www_authenticate='Bearer realm="node-wire"',
        )


class McpAuthInvalidError(McpAuthError):
    def __init__(self) -> None:
        super().__init__(
            "Invalid API key or token",
            status_code=403,
            error_code="MCP_AUTH_INVALID",
            www_authenticate='Bearer realm="node-wire"',
        )


class McpAuthNotConfiguredError(McpAuthError):
    def __init__(self) -> None:
        super().__init__(
            (
                "MCP authentication is not configured. Set NW_MCP_API_KEY "
                "(and optionally NW_MCP_JWT_SECRET), or set NW_MCP_AUTH_DISABLED=true "
                "for local development only."
            ),
            status_code=503,
            error_code="MCP_AUTH_NOT_CONFIGURED",
        )


_mcp_auth_env_bootstrapped = False


def _bootstrap_mcp_auth_env() -> None:
    global _mcp_auth_env_bootstrapped
    if _mcp_auth_env_bootstrapped:
        return

    # Some launch paths on Windows can miss .env loading for the MCP worker.
    # If MCP auth vars are missing/empty, try loading project .env once.
    if os.environ.get("NW_MCP_API_KEY") or os.environ.get("NW_MCP_JWT_SECRET"):
        _mcp_auth_env_bootstrapped = True
        return

    # Align with REST/bindings: when dotenv merge is disabled (pytest, CI, prod),
    # never load repo `.env` with override=True — that stomps conftest env and
    # monkeypatched values (e.g. NW_ALLOWED_CONNECTORS, NW_MCP_AUTH_ENABLED).
    rest_dotenv = os.environ.get("NW_REST_LOAD_DOTENV", "true").lower()
    if rest_dotenv in ("0", "false", "no"):
        # Keys may be injected later (tests); do not mark bootstrapped so we recheck.
        return

    repo_root_env = Path(__file__).resolve().parents[3] / ".env"
    load_dotenv(override=False)
    load_dotenv(repo_root_env, override=False)
    _mcp_auth_env_bootstrapped = True


def mcp_auth_disabled() -> bool:
    """Return ``True`` when MCP authentication is disabled.

    The canonical flag is ``NW_MCP_AUTH_DISABLED`` (truthy disables auth),
    matching ``NW_REST_AUTH_DISABLED`` / ``NW_GRPC_AUTH_DISABLED`` across the
    other bindings. The default (unset) keeps authentication **enabled**.

    ``NW_MCP_AUTH_ENABLED`` is a deprecated legacy flag whose original
    implementation inverted its own name — setting it to ``true`` *disabled*
    authentication, the opposite of what an operator would expect. It is now
    honored with its literal meaning (``false``/``0``/``no`` disables auth;
    anything else keeps it enabled) and emits a deprecation warning.
    ``NW_MCP_AUTH_DISABLED`` takes precedence when both are set.
    """
    disabled = os.environ.get("NW_MCP_AUTH_DISABLED")
    if disabled is not None and disabled.strip():
        return _truthy(disabled)

    legacy = os.environ.get("NW_MCP_AUTH_ENABLED")
    if legacy is not None and legacy.strip():
        enabled = _truthy(legacy)
        logger.warning(
            "NW_MCP_AUTH_ENABLED is deprecated and its semantics have been "
            "corrected; use NW_MCP_AUTH_DISABLED instead. Effective MCP auth "
            "state: %s.",
            "ENABLED" if enabled else "DISABLED",
        )
        return not enabled

    return False


def log_effective_mcp_auth_state() -> None:
    """Emit a single, explicit startup line describing the MCP auth posture."""
    disabled = mcp_auth_disabled()
    logger.warning(
        "MCP authentication is %s",
        "DISABLED (local development only — do not use in production)" if disabled else "ENABLED",
        extra={
            "auth_disabled": disabled,
            "auth_configured": mcp_auth_configured(),
        },
    )


def mcp_auth_configured() -> bool:
    _bootstrap_mcp_auth_env()
    return bool(os.environ.get("NW_MCP_API_KEY") or os.environ.get("NW_MCP_JWT_SECRET"))


def _get_meta_value(meta: Mapping[str, Any] | None, keys: tuple[str, ...]) -> str | None:
    if not meta:
        return None
    for key in keys:
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_token(
    *,
    headers: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> str | None:
    if headers:
        auth = headers.get("authorization") or headers.get("Authorization")
        if isinstance(auth, str) and auth.lower().startswith("bearer "):
            return auth[7:].strip()
        x_api_key = headers.get("x-api-key") or headers.get("X-API-Key")
        if isinstance(x_api_key, str) and x_api_key.strip():
            return x_api_key.strip()

    auth_meta = _get_meta_value(meta, ("authorization", "Authorization"))
    if auth_meta and auth_meta.lower().startswith("bearer "):
        return auth_meta[7:].strip()

    return _get_meta_value(meta, ("x-api-key", "X-API-Key", "api_key", "apiKey", "token"))


def verify_mcp_token(token: str) -> tuple[dict[str, Any], str]:
    api_key = os.getenv("NW_MCP_API_KEY")
    jwt_secret = os.getenv("NW_MCP_JWT_SECRET")

    if api_key and api_key_matches(token, api_key):
        scopes = list(parse_api_key_scopes_from_env("NW_MCP_API_KEY_SCOPES"))
        return ({"sub": "api-key-user", "tenant_id": None, "scopes": scopes}, "api_key")

    if jwt_secret and token.count(".") == 2:
        try:
            claims = decode_binding_jwt(token, jwt_secret)
            logger.info("MCP token verified as JWT")
            return (claims, "jwt")
        except jwt.PyJWTError as exc:
            raise McpAuthInvalidError() from exc

    raise McpAuthInvalidError()


def build_identity(claims: Mapping[str, Any], auth_type: str) -> CallerIdentity:
    """Deprecated alias for :func:`build_caller_identity`; prefer that name in new code."""
    return build_caller_identity(claims, auth_type)


def authenticate_mcp_request(
    *,
    headers: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
    upstream_passthrough: bool = False,
    upstream_granted_scopes: tuple[str, ...] = (),
) -> CallerIdentity | None:
    if upstream_passthrough:
        token = extract_token(headers=headers, meta=meta)
        if not token:
            if mcp_auth_disabled():
                return None
            raise McpAuthRequiredError()
        _upstream_reset_ctx.set(set_upstream_bearer(token))
        if mcp_auth_disabled():
            return None
        # Ponytail: MCP scopes gate tool visibility on this server; the Google OAuth
        # access token on the request is the upstream authz boundary for Drive API.
        identity = build_caller_identity(
            {
                "sub": "upstream-bearer",
                "tenant_id": None,
                "scopes": list(upstream_granted_scopes),
            },
            "upstream_bearer",
        )
        logger.info(
            "MCP upstream passthrough accepted",
            extra={"auth_type": identity.auth_type, "principal": identity.principal},
        )
        return identity

    logger.info(
        "MCP auth gate status",
        extra={
            "auth_disabled": mcp_auth_disabled(),
            "auth_configured": mcp_auth_configured(),
            "has_api_key": bool(os.environ.get("NW_MCP_API_KEY")),
            "has_jwt_secret": bool(os.environ.get("NW_MCP_JWT_SECRET")),
        },
    )
    if mcp_auth_disabled():
        return None

    if not mcp_auth_configured():
        raise McpAuthNotConfiguredError()

    token = extract_token(headers=headers, meta=meta)
    if not token:
        raise McpAuthRequiredError()

    claims, auth_type = verify_mcp_token(token)
    identity = build_caller_identity(claims, auth_type)
    logger.info(
        "MCP auth accepted",
        extra={
            "auth_type": identity.auth_type,
            "principal": identity.principal,
            "tenant_id": identity.tenant_id or "",
            "scopes": list(identity.scopes),
        },
    )
    return identity


def reset_upstream_passthrough_context() -> None:
    """Clear upstream bearer set during passthrough auth (call in middleware finally)."""
    reset_tok = _upstream_reset_ctx.get()
    if reset_tok is not None:
        reset_upstream_bearer(reset_tok)
        _upstream_reset_ctx.set(None)
