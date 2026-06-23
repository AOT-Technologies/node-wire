"""Transport-neutral caller identity for connector execution and policy hooks."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

import jwt


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
    if api_key and token == api_key:
        scopes = list(parse_api_key_scopes_from_env(api_key_scopes_env))
        ident = build_caller_identity(
            {"sub": "api-key-user", "tenant_id": None, "scopes": scopes},
            auth_type=api_key_auth_type,
        )
        return True, ident

    if jwt_secret and token.count(".") == 2:
        try:
            claims = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            return False, None
        return True, build_caller_identity(claims, auth_type="jwt")

    return False, None
