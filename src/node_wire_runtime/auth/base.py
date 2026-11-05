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

import asyncio
import contextvars
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, TypeVar

_T = TypeVar("_T")


async def provider_io_off_loop(provider: Any, fn: Callable[[], _T]) -> _T:
    """Run ``fn`` (which reads from ``provider``) without blocking the event loop.

    Network-backed providers (Azure Key Vault) set ``blocking_io``; their reads go to a
    thread. In-memory providers run inline, so they pay no thread hop.
    """
    if getattr(provider, "blocking_io", False):
        return await asyncio.to_thread(fn)
    return fn()


async def read_secrets_off_loop(secret_provider: Any, fn: Callable[[], _T]) -> _T:
    """:func:`provider_io_off_loop` for reads that return credentials.

    Use :func:`provider_io_off_loop` for non-secret config (token URL, client id) that is
    safe to log.
    """
    return await provider_io_off_loop(secret_provider, fn)


_upstream_bearer_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_upstream_bearer", default=None
)


def get_upstream_bearer() -> str | None:
    """Bearer token from the current MCP request (upstream OIDC passthrough)."""
    return _upstream_bearer_ctx.get()


def set_upstream_bearer(token: str | None) -> contextvars.Token:
    return _upstream_bearer_ctx.set(token)


def reset_upstream_bearer(ctx_token: contextvars.Token) -> None:
    _upstream_bearer_ctx.reset(ctx_token)


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

    async def get_query_params(self) -> Dict[str, str]:
        """Return query-string auth parameters (default: none).

        Override in providers that authenticate via query parameters
        (e.g. :class:`~node_wire_runtime.auth.apikey_query.ApiKeyQueryAuthProvider`).
        """
        return {}

    async def refresh(self) -> None:
        """
        Force a refresh of any cached credentials on the next call.

        The default is a no-op; override in providers that maintain a cache
        (e.g. :class:`~node_wire_runtime.auth.oauth2.OAuth2AuthProvider`).

        Call this after receiving a 401/403 to ensure the next request uses
        freshly-issued credentials.
        """
