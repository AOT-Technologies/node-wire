#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.auth.base
============================

Abstract base class for all authentication providers.

All authentication falls into two categories:

  Static Credentials  — fixed secrets (API keys, service-account JSON, SMTP passwords).
  Dynamic Tokens      — short-lived tokens that must be fetched and cached.

Both are unified behind this interface so connectors stay credential-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class AuthProvider(ABC):
    """
    Abstract port for connector authentication.

    Connectors call :meth:`get_headers` to obtain ready-to-use HTTP request
    headers and :meth:`get_client_credentials` when they need SDK-level
    objects (e.g. ``google.oauth2.credentials.Credentials``).

    Override :meth:`refresh` to force credential renewal after a 401.
    """

    @abstractmethod
    async def get_headers(self) -> Dict[str, str]:
        """
        Return a dict of HTTP headers required to authenticate the request.

        For bearer-token flows this is ``{"Authorization": "Bearer <token>"}``.
        For connectors that authenticate at the SDK level (e.g. Google Drive),
        this may return an empty dict.

        Implementations are responsible for caching and refreshing tokens
        transparently; callers must not cache the result themselves.
        """
        raise NotImplementedError

    async def get_client_credentials(self) -> Any:
        """
        Return SDK-level credentials (e.g. ``google.oauth2.Credentials``).

        The default implementation returns ``None``; override in providers
        that need to supply credentials to vendor SDKs rather than HTTP headers.
        """
        return None

    async def refresh(self) -> None:
        """
        Force a refresh of any cached credentials on the next call.

        The default is a no-op; override in providers that maintain a cache
        (e.g. :class:`~node_wire_runtime.auth.oauth2.OAuth2AuthProvider`).

        Call this after receiving a 401/403 to ensure the next request uses
        freshly-issued credentials.
        """
