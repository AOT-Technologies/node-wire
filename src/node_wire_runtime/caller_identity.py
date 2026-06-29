#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Transport-neutral caller identity for connector execution and policy hooks."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

import jwt

logger = logging.getLogger("runtime.caller_identity")

JWT_AUDIENCE_ENV = "NW_JWT_AUDIENCE"
JWT_ISSUER_ENV = "NW_JWT_ISSUER"

_jwt_aud_iss_warned = False


@dataclass(frozen=True)
class CallerIdentity:
    """Who is calling ``connector.run`` (REST, MCP, or other bindings)."""

    principal: str
    tenant_id: str | None
    scopes: tuple[str, ...]
    claims: Mapping[str, Any]
    auth_type: str


def build_caller_identity(claims: Mapping[str, Any], auth_type: str) -> CallerIdentity:
    """Build identity from JWT-style claims (``sub``, ``tenant_id``, ``scopes`` / ``scope``)."""
    principal = str(claims.get("sub") or claims.get("client_id") or "unknown")
    tenant_val = claims.get("tenant_id")
    tenant_id = str(tenant_val) if tenant_val is not None else None
    raw_scopes = claims.get("scopes")
    if raw_scopes is None:
        raw_scopes = claims.get("scope")
    if isinstance(raw_scopes, str):
        scopes = tuple(s for s in raw_scopes.split(" ") if s)
    elif isinstance(raw_scopes, (list, tuple, set)):
        scopes = tuple(str(s) for s in raw_scopes if str(s).strip())
    else:
        scopes = tuple()
    return CallerIdentity(
        principal=principal,
        tenant_id=tenant_id,
        scopes=scopes,
        claims=dict(claims),
        auth_type=auth_type,
    )


def parse_api_key_scopes_from_env(env_var: str) -> tuple[str, ...]:
    """
    Parse scopes for shared API keys (MCP / REST), e.g. ``NW_MCP_API_KEY_SCOPES``.

    Accepts:

    - JSON array: ``["mcp:smtp.send_email","mcp:other"]``
    - Whitespace or comma separated tokens: ``mcp:a mcp:b`` or ``mcp:a,mcp:b``

    Empty / unset means **no** scopes (not wildcard).
    """
    raw = os.environ.get(env_var)
    if raw is None or not str(raw).strip():
        return tuple()
    raw = str(raw).strip()
    if raw.startswith("["):
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError(f"{env_var} JSON must be an array of strings")
        return tuple(str(s).strip() for s in parsed if str(s).strip())
    return tuple(p for p in re.split(r"[\s,]+", raw) if p)


def api_key_matches(token: str, api_key: str) -> bool:
    """Constant-time comparison of presented token and configured API key."""
    return hmac.compare_digest(token, api_key)


class JwtVerificationNotConfigured(jwt.InvalidTokenError):
    """Raised when a JWT secret is used but ``NW_JWT_AUDIENCE`` / ``NW_JWT_ISSUER`` are unset."""


def load_jwt_audience_issuer_from_env() -> tuple[str, str] | None:
    """Return ``(audience, issuer)`` when both shared JWT env vars are set."""
    audience = os.environ.get(JWT_AUDIENCE_ENV)
    issuer = os.environ.get(JWT_ISSUER_ENV)
    if not audience or not str(audience).strip():
        return None
    if not issuer or not str(issuer).strip():
        return None
    return str(audience).strip(), str(issuer).strip()


def warn_jwt_audience_issuer_not_configured() -> None:
    global _jwt_aud_iss_warned
    if _jwt_aud_iss_warned:
        return
    _jwt_aud_iss_warned = True
    logger.warning(
        "JWT secret is configured but %s and %s are not set; JWT verification will fail",
        JWT_AUDIENCE_ENV,
        JWT_ISSUER_ENV,
    )


def decode_binding_jwt(token: str, secret: str) -> dict[str, Any]:
    """
    Verify an HS256 ingress JWT for MCP / REST / gRPC bindings.

    Requires ``exp`` and ``iat`` claims and validates ``aud`` / ``iss`` against env.
    """
    aud_iss = load_jwt_audience_issuer_from_env()
    if aud_iss is None:
        warn_jwt_audience_issuer_not_configured()
        raise JwtVerificationNotConfigured(
            f"{JWT_AUDIENCE_ENV} and {JWT_ISSUER_ENV} must be set when JWT authentication is enabled"
        )
    audience, issuer = aud_iss
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iat"]},
    )


def verify_bearer_token_and_identity(
    token: str,
    *,
    api_key: str | None,
    jwt_secret: str | None,
    api_key_scopes_env: str,
    api_key_auth_type: str,
) -> tuple[bool, CallerIdentity | None]:
    """
    Validate a bearer/API-key token and build caller identity.

    Shared by REST and gRPC bindings. API key scopes come from ``api_key_scopes_env``.
    """
    if api_key and api_key_matches(token, api_key):
        scopes = list(parse_api_key_scopes_from_env(api_key_scopes_env))
        ident = build_caller_identity(
            {"sub": "api-key-user", "tenant_id": None, "scopes": scopes},
            auth_type=api_key_auth_type,
        )
        return True, ident

    if jwt_secret and token.count(".") == 2:
        try:
            claims = decode_binding_jwt(token, jwt_secret)
        except jwt.PyJWTError:
            return False, None
        return True, build_caller_identity(claims, auth_type="jwt")

    return False, None
