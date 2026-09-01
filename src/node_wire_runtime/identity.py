#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.identity
==========================

Header/argument-based tenant identity for the embedding application.

Node Wire is a library embedded in a trusted host. The tenant id it supplies is
taken at face value (the ``X-Tenant-ID`` header is fully trusted, no verification).
If bindings are exposed to untrusted clients, deploy an authenticating gateway in
front; node-wire itself performs no authentication here.

Resolution order when NW_MULTITENANCY_ENABLED=true (see plan decision C / C2):
    1. ``env_pin``            transport with no headers (MCP stdio ``NW_TENANT_ID``)
    2. header                 ``X-Tenant-ID`` / ``NW_TENANT_ID_HEADER`` (case-insensitive)
    3. ``jwt_identity``       existing JWT-claim tenancy (unchanged)
    4. raise MissingTenantError when none of the above are present

When NW_MULTITENANCY_ENABLED=false (default), all calls return DEFAULT_TENANT
regardless of headers, JWT, or env_pin so connectors behave exactly as before
multi-tenancy was introduced.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from node_wire_runtime.config_store import DEFAULT_TENANT

# Header name lookup is case-insensitive; store the configured name lowercased.
TENANT_HEADER = os.getenv("NW_TENANT_ID_HEADER", "x-tenant-id").lower()

MISSING_TENANT_MESSAGE = "X-Tenant-ID is required when multitenancy is enabled"


class MissingTenantError(ValueError):
    """Raised when multitenancy is enabled and no tenant id can be resolved."""

    def __init__(self, message: str = MISSING_TENANT_MESSAGE) -> None:
        super().__init__(message)


def is_multitenancy_enabled() -> bool:
    """Return True when NW_MULTITENANCY_ENABLED is set to a truthy value.

    Defaults to False so existing single-tenant deployments are unaffected.
    """
    return os.getenv("NW_MULTITENANCY_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def tenant_from_headers(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    """Return the tenant id from a case-insensitive header lookup, or ``None``.

    An empty / whitespace-only value is treated as absent.
    """
    if not headers:
        return None
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() == TENANT_HEADER:
            if v is None:
                return None
            stripped = v.strip()
            return stripped or None
    return None


def resolve_tenant_id(
    *,
    headers: Optional[Mapping[str, str]] = None,
    jwt_identity: Any = None,
    env_pin: Optional[str] = None,
) -> str:
    """Resolve the effective tenant id.

    When NW_MULTITENANCY_ENABLED is false (default), always returns DEFAULT_TENANT
    regardless of inputs so connectors behave as legacy single-tenant.

    When enabled, requires env_pin, header, or jwt tenant claim; otherwise raises
    :class:`MissingTenantError`. Explicit ``__default__`` in the header is allowed.

    ``jwt_identity`` is any object exposing a ``tenant_id`` attribute (e.g.
    :class:`~node_wire_runtime.caller_identity.CallerIdentity`).
    """
    # Simplified: early exit keeps all callers unchanged; no second code path.
    if not is_multitenancy_enabled():
        return DEFAULT_TENANT

    if env_pin is not None:
        pinned = env_pin.strip()
        if pinned:
            return pinned

    header_tenant = tenant_from_headers(headers)
    if header_tenant:
        return header_tenant

    if jwt_identity is not None:
        claim = getattr(jwt_identity, "tenant_id", None)
        if claim is not None and str(claim).strip():
            return str(claim).strip()

    raise MissingTenantError()


def resolve_config_name(config_name: Optional[str]) -> Optional[str]:
    """Return a non-empty config name when multitenancy is enabled, else None.

    Ensures user-supplied named configs are silently ignored in single-tenant
    mode so the factory falls back to the YAML-bootstrapped default.

    ``None``, non-strings, and blank strings are treated as omit (tenant default).
    LLMs often emit ``config_name: null`` for optional fields; that must not
    fail closed as an unknown name.
    """
    if not is_multitenancy_enabled():
        return None
    if not isinstance(config_name, str):
        return None
    stripped = config_name.strip()
    return stripped or None
