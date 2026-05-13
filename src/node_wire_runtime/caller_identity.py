"""Transport-neutral caller identity for connector execution and policy hooks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


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
