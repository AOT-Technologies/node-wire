from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

import jwt


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class McpIdentity:
    principal: str
    tenant_id: str | None
    scopes: tuple[str, ...]
    claims: Mapping[str, Any]
    auth_type: str


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


def mcp_auth_disabled() -> bool:
    return _truthy(os.environ.get("NW_MCP_AUTH_DISABLED"))


def mcp_auth_configured() -> bool:
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

    if api_key and token == api_key:
        return ({"sub": "api-key-user", "tenant_id": None, "scopes": ["*"]}, "api_key")

    if jwt_secret and token.count(".") == 2:
        try:
            claims = jwt.decode(token, jwt_secret, algorithms=["HS256"])
            return (claims, "jwt")
        except jwt.PyJWTError as exc:
            raise McpAuthInvalidError() from exc

    raise McpAuthInvalidError()


def build_identity(claims: Mapping[str, Any], auth_type: str) -> McpIdentity:
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
    return McpIdentity(
        principal=principal,
        tenant_id=tenant_id,
        scopes=scopes,
        claims=dict(claims),
        auth_type=auth_type,
    )


def authenticate_mcp_request(
    *,
    headers: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> McpIdentity | None:
    if mcp_auth_disabled():
        return None

    if not mcp_auth_configured():
        raise McpAuthNotConfiguredError()

    token = extract_token(headers=headers, meta=meta)
    if not token:
        raise McpAuthRequiredError()

    claims, auth_type = verify_mcp_token(token)
    return build_identity(claims, auth_type)
