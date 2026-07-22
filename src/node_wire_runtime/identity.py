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

Resolution order (see plan decision C / C2):
    1. ``env_pin``            transport with no headers (MCP stdio ``NW_TENANT_ID``)
    2. header                 ``X-Tenant-ID`` / ``NW_TENANT_ID_HEADER`` (case-insensitive)
    3. ``jwt_identity``       existing JWT-claim tenancy (unchanged)
    4. ``DEFAULT_TENANT``     sentinel ``"__default__"``
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from node_wire_runtime.config_store import DEFAULT_TENANT

# Header name lookup is case-insensitive; store the configured name lowercased.
TENANT_HEADER = os.getenv("NW_TENANT_ID_HEADER", "x-tenant-id").lower()


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
    """Resolve the effective tenant id, never raising on a missing header.

    ``jwt_identity`` is any object exposing a ``tenant_id`` attribute (e.g.
    :class:`~node_wire_runtime.caller_identity.CallerIdentity`). ``None`` for the
    legacy API-key path is normalized to :data:`DEFAULT_TENANT`.
    """
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

    return DEFAULT_TENANT
