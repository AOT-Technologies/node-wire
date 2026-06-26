#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.auth.no_auth
================================

Null-object implementation of :class:`AuthProvider`.

Returns empty headers and ``None`` credentials. Acts as the safe default
when no ``auth:`` block is present in ``connectors.yaml``, ensuring connectors
never receive ``None`` and never need to guard against an unconfigured provider.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import AuthProvider


class NoAuthProvider(AuthProvider):
    """
    No-op authentication provider.

    Suitable for connectors that handle auth at the SDK level in a custom
    ``build_client()`` override, or for internal/localhost endpoints that
    require no credentials.
    """

    async def get_headers(self) -> Dict[str, str]:
        """Return an empty dict — no auth headers injected."""
        return {}

    async def get_client_credentials(self) -> Any:
        """Return ``None`` — no SDK credentials required."""
        return None
