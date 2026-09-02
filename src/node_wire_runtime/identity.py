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
    2. ``jwt_identity``       authenticated JWT-claim tenancy, when present — authoritative
    3. header                 ``X-Tenant-ID`` / ``NW_TENANT_ID_HEADER`` (case-insensitive),
                               used only when the JWT carries no tenant claim of its own
    4. raise MissingTenantError when none of the above are present

Security note (H-1, 2026-09-01 review): the header/metadata value is supplied
by the caller and is not authenticated by node-wire (see the module docstring
above). When a JWT identity *does* carry a ``tenant_id`` claim, that claim is
authoritative and the header is only accepted if it agrees with it — a caller
authenticated for tenant A can no longer point ``X-Tenant-ID``/gRPC metadata
at tenant B to redirect config/secret lookups and connector calls to a tenant
it wasn't issued a token for. See :class:`TenantIdentityMismatchError`.

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


class TenantMismatchError(Exception):
    """Factory instance tenant disagrees with ``run(tenant_id=...)``."""

    def __init__(self, *, pinned: str, requested: str) -> None:
        self.pinned = pinned
        self.requested = requested
        super().__init__(
            f"tenant mismatch: instance pinned to {pinned!r}, run requested {requested!r}"
        )


class TenantIdentityMismatchError(ValueError):
    """Caller-supplied tenant header/metadata disagrees with the JWT's own tenant claim.

    Raised by :func:`resolve_tenant_id` when ``jwt_identity`` carries a
    ``tenant_id`` claim and the request also supplies a tenant header/metadata
    value for a *different* tenant. The header is untrusted caller input (see
    module docstring); the JWT claim is what the embedding application's auth
    layer actually authenticated, so it wins and the mismatch is rejected
    outright rather than silently preferring the header (see H-1, 2026-09-01
    security review).
    """

    def __init__(self, *, jwt_tenant: str, header_tenant: str) -> None:
        self.jwt_tenant = jwt_tenant
        self.header_tenant = header_tenant
        super().__init__(
            "tenant header/metadata "
            f"{header_tenant!r} does not match authenticated tenant {jwt_tenant!r}"
        )


def normalize_tenant_id(value: Optional[str]) -> Optional[str]:
    """Return a stripped tenant id, or ``None`` when absent / blank."""
    if value is None:
        return None
    if not isinstance(value, str):
        stripped = str(value).strip()
        return stripped or None
    stripped = value.strip()
    return stripped or None


def tenants_equivalent(a: Optional[str], b: Optional[str]) -> bool:
    """Treat ``None`` and ``__default__`` as the same tenant id."""
    left = normalize_tenant_id(a) or DEFAULT_TENANT
    right = normalize_tenant_id(b) or DEFAULT_TENANT
    return left == right


def effective_run_tenant_id(
    *,
    pinned: Optional[str],
    caller: Optional[str],
) -> tuple[Optional[str], Optional[TenantMismatchError]]:
    """Resolve the tenant id for :meth:`~node_wire_runtime.base_connector.BaseConnector.run`.

    When the instance was built by the factory (``pinned`` set), ``caller`` must
    agree or be omitted; omission uses the pin. Unpinned instances keep legacy
    ``run(tenant_id=...)`` behavior.
    """
    normalized_caller = normalize_tenant_id(caller)
    if pinned is not None:
        if normalized_caller is None:
            return pinned, None
        if not tenants_equivalent(normalized_caller, pinned):
            requested = normalized_caller or DEFAULT_TENANT
            return None, TenantMismatchError(pinned=pinned, requested=requested)
        return pinned, None
    return normalized_caller, None


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

    When enabled, requires env_pin, jwt tenant claim, or header; otherwise raises
    :class:`MissingTenantError`. Explicit ``__default__`` in the header is allowed.

    ``jwt_identity`` is any object exposing a ``tenant_id`` attribute (e.g.
    :class:`~node_wire_runtime.caller_identity.CallerIdentity`). When it carries a
    tenant claim, that claim is authoritative: a header/metadata value for a
    *different* tenant is rejected with :class:`TenantIdentityMismatchError`
    rather than silently overriding it (see H-1, 2026-09-01 security review).
    A header that agrees with the claim (or is simply absent) is fine.
    """
    # Simplified: early exit keeps all callers unchanged; no second code path.
    if not is_multitenancy_enabled():
        return DEFAULT_TENANT

    if env_pin is not None:
        pinned = env_pin.strip()
        if pinned:
            return pinned

    header_tenant = tenant_from_headers(headers)

    jwt_tenant: Optional[str] = None
    if jwt_identity is not None:
        claim = getattr(jwt_identity, "tenant_id", None)
        if claim is not None and str(claim).strip():
            jwt_tenant = str(claim).strip()

    if jwt_tenant is not None:
        if header_tenant is not None and not tenants_equivalent(header_tenant, jwt_tenant):
            raise TenantIdentityMismatchError(jwt_tenant=jwt_tenant, header_tenant=header_tenant)
        return jwt_tenant

    if header_tenant:
        return header_tenant

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
