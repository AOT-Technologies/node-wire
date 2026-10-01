# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Relay the per-request ToolHive credential into a connector's own auth placement.

The credential is read on every call (never cached) from the runtime's upstream-bearer context,
which :class:`~node_wire_toolhive.client.NodeWireClient` sets for the duration of one action.
Caching would be wrong here: the factory keeps one connector instance per (tenant, config), so a
cached credential would be reused for the next caller of that instance.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from bindings.factory import AuthProviderHook
from node_wire_runtime.auth import StaticTokenAuthProvider
from node_wire_runtime.auth.base import AuthProvider, get_upstream_bearer
from node_wire_runtime.errors import CATALOGUE, ErrorCode, ErrorMapper
from node_wire_runtime.secrets import SecretNotFoundError, SecretProvider

RELAY_ALLOWLIST_ENV = "NW_UPSTREAM_BEARER_CONNECTORS"

# Providers whose credential ToolHive supplies as a bearer token: ToolHive ran the OAuth flow
# (oauth2 / service_account) or already forwards the caller's token (upstream_bearer).
_BEARER_PROVIDERS = frozenset({"oauth2", "service_account", "upstream_bearer"})
_TOKEN_KEY = "toolhive_upstream_token"  # nosec B105  # logical key name, not a credential


class MissingUpstreamTokenError(RuntimeError):
    """The request carried no upstream credential for a connector that needs one."""


# The caller (ToolHive) forwarded no credential: an authentication failure on its side.
ErrorMapper.register_global(
    MissingUpstreamTokenError,
    CATALOGUE[ErrorCode.UPSTREAM_TOKEN_MISSING],
    code=ErrorCode.UPSTREAM_TOKEN_MISSING,
)


class _CurrentToken(SecretProvider):
    """Resolves the one logical key to the credential of the request being handled."""

    def get_secret(self, key: str) -> str:
        token = get_upstream_bearer()
        if key != _TOKEN_KEY or not token:
            raise SecretNotFoundError(key)
        return token


def _require_token() -> str:
    token = get_upstream_bearer()
    if not token:
        raise MissingUpstreamTokenError(
            "No upstream credential on this request: ToolHive must forward "
            "'Authorization: Bearer <token>' to the MCP server."
        )
    return token


class RelayAuthProvider(AuthProvider):
    """Present the current request's credential the way ``auth_cfg`` says the API expects it.

    ``static_token`` keeps its ``header_name`` / ``prefix`` / ``encoding``; ``apikey_query``
    keeps its query parameter ``name``; OAuth2, service-account and upstream-bearer schemes
    become ``Authorization: Bearer``.
    """

    per_request_credentials = True

    def __init__(self, auth_cfg: Dict[str, Any]) -> None:
        provider = str(auth_cfg.get("provider") or "")
        self._query_name: Optional[str] = None
        self._header: Optional[StaticTokenAuthProvider] = None
        if provider == "apikey_query":
            self._query_name = str(auth_cfg["name"])
        elif provider == "static_token":
            self._header = StaticTokenAuthProvider(
                secret_provider=_CurrentToken(),
                secret_key=_TOKEN_KEY,
                header_name=auth_cfg.get("header_name", "Authorization"),
                prefix=auth_cfg.get("prefix", "Bearer"),
                encoding=auth_cfg.get("encoding"),
                cache=False,
            )
        elif provider in _BEARER_PROVIDERS:
            self._header = StaticTokenAuthProvider(
                secret_provider=_CurrentToken(), secret_key=_TOKEN_KEY, cache=False
            )
        else:
            raise ValueError(
                f"auth provider {provider!r} cannot take a ToolHive-forwarded credential"
            )

    async def get_headers(self) -> Dict[str, str]:
        if self._header is None:
            return {}
        _require_token()
        return await self._header.get_headers()

    async def get_query_params(self) -> Dict[str, str]:
        if self._query_name is None:
            return {}
        return {self._query_name: _require_token()}

    async def get_client_credentials(self) -> Any:
        """SDK-level credentials for connectors that call vendor SDKs (Google APIs)."""
        token = _require_token()
        try:
            from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        except ImportError:
            return None
        return Credentials(token=token)


def _relay_allowlist() -> frozenset[str]:
    raw = os.environ.get(RELAY_ALLOWLIST_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def relay_auth_provider_hook() -> AuthProviderHook:
    """A :class:`~bindings.factory.ConnectorFactory` hook that relays ToolHive's credential.

    Fail-closed opt-in, like the factory's own ``upstream_bearer``: a connector must be listed in
    ``NW_UPSTREAM_BEARER_CONNECTORS`` before a caller's credential is relayed to its API. A
    connector with no auth (``provider: none``) keeps the default provider.
    """

    def hook(connector_id: str, auth_cfg: Dict[str, Any]) -> Optional[AuthProvider]:
        if str(auth_cfg.get("provider") or "none") in ("none", ""):
            return None
        if connector_id not in _relay_allowlist():
            raise ValueError(
                f"connector {connector_id!r} would relay the caller's credential but is not in "
                f"{RELAY_ALLOWLIST_ENV}; add it there to opt in"
            )
        return RelayAuthProvider(auth_cfg)

    return hook
