#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Transport-neutral caller identity for connector execution and policy hooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import jwt

from node_wire_runtime.config_store import DEFAULT_TENANT

logger = logging.getLogger("runtime.caller_identity")

API_KEYS_FILE_ENV = "NW_API_KEYS_FILE"

JWT_AUDIENCE_ENV = "NW_JWT_AUDIENCE"
JWT_ISSUER_ENV = "NW_JWT_ISSUER"


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


def api_key_registry_configured() -> bool:
    return bool(os.environ.get(API_KEYS_FILE_ENV, "").strip())


_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")


def _registry_entry(index: int, entry: Any) -> dict[str, Any]:
    """One validated, normalised registry entry. Errors name the entry and never echo its hash."""
    where = f"{API_KEYS_FILE_ENV}: entry {index}"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a JSON object")
    for field in ("key_id", "hash", "tenant"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{where} needs a non-empty string {field!r}")
    key_id, digest, tenant = (entry[field].strip() for field in ("key_id", "hash", "tenant"))
    if not _SHA256_HEX.fullmatch(digest):
        raise ValueError(f"{where} ({key_id!r}): 'hash' must be a SHA-256 hex digest")
    # A blank tenant would make a tenant-less key, which can pick any tenant by header.
    if tenant == DEFAULT_TENANT:
        raise ValueError(f"{where} ({key_id!r}) cannot bind {DEFAULT_TENANT!r}")
    scopes = entry.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ValueError(f"{where} ({key_id!r}): 'scopes' must be a list of strings")
    return {"key_id": key_id, "hash": digest.lower(), "tenant": tenant, "scopes": scopes}


@lru_cache(maxsize=None)
def _read_api_key_registry(path: str) -> tuple[Mapping[str, Any], ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise ValueError(
            f"{API_KEYS_FILE_ENV}: cannot read the key registry ({type(exc).__name__})"
        ) from exc
    if not isinstance(raw, list):
        raise ValueError(f"{API_KEYS_FILE_ENV} must contain a JSON array")
    entries = [_registry_entry(index, entry) for index, entry in enumerate(raw)]
    # The same key twice would bind it to whichever tenant comes first.
    for field in ("hash", "key_id"):
        values = [entry[field] for entry in entries]
        if len(set(values)) != len(values):
            raise ValueError(f"{API_KEYS_FILE_ENV}: two entries share the same {field!r}")
    return tuple(entries)


def load_api_key_registry() -> tuple[Mapping[str, Any], ...]:
    """Tenant-bound keys from ``NW_API_KEYS_FILE``: ``[{key_id, hash, tenant, scopes}]``.

    Every entry is validated here (non-blank ``key_id`` / ``hash`` / ``tenant``, a SHA-256 hex
    ``hash``, ``scopes`` a list, no ``__default__``, no duplicates) so a typo in the file fails
    startup naming the entry, instead of becoming a tenant-less key or a ``KeyError`` per request.

    ``hash`` is the hex SHA-256 of the key; the key itself is never stored. Simplified: read
    once per path and cached; changes need a restart.
    """
    return _read_api_key_registry(os.environ[API_KEYS_FILE_ENV].strip())


def api_key_registry_claims(token: str) -> dict[str, Any] | None:
    """JWT-style claims for a registry key matching ``token``, else ``None``."""
    if not api_key_registry_configured():
        return None
    # Simplified: plain SHA-256, not a password KDF. Registry keys are machine-generated
    # high-entropy secrets (``openssl rand -hex 32``), not human passwords, so there is nothing
    # to brute-force; a slow KDF would only add ~300 ms of event-loop-blocking CPU to every
    # request with a bad token. Same trade-off as GitHub/Stripe-style API-key storage.
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    for entry in load_api_key_registry():
        if hmac.compare_digest(digest, entry["hash"]):
            return {
                "sub": entry["key_id"],
                "tenant_id": entry["tenant"],
                "scopes": entry["scopes"],
            }
    return None


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


@lru_cache(maxsize=None)
def warn_jwt_audience_issuer_not_configured() -> None:
    """Warn once per process; ``lru_cache`` suppresses repeats."""
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
    registry_claims = api_key_registry_claims(token)
    if registry_claims is not None:
        return True, build_caller_identity(registry_claims, auth_type=api_key_auth_type)

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
