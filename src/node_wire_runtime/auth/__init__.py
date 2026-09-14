#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
node_wire_runtime.auth
=======================

Pluggable authentication layer for Node Wire connectors.

All providers implement :class:`AuthProvider` and are safe to inject into any
:class:`~node_wire_runtime.base_connector.BaseConnector` subclass via the
``auth_provider=`` constructor argument.

Available providers
-------------------
NoAuthProvider
    Null-object — returns empty headers. Default when no ``auth:`` block is
    present in ``connectors.yaml``.

StaticTokenAuthProvider
    Reads a single secret via :class:`~node_wire_runtime.secrets.SecretProvider`
    and injects it as ``Authorization: Bearer <token>`` (or a custom header).
    Optionally base64-encodes the value for HTTP Basic auth.

OAuth2AuthProvider
    Fetches and caches OAuth 2.0 access tokens. Supports ``private_key_jwt``
    (SMART Backend Services / Epic / Cerner), ``client_secret_post`` (app-only
    client credentials), and ``refresh_token`` (the non-interactive tail end
    of an authorization_code flow — e.g. Salesforce). Uses ``asyncio.Lock`` to
    prevent concurrent token-refresh storms. For ``refresh_token``, an
    optional ``on_refresh_token_rotated`` callback lets the host app persist
    a replacement refresh token when the IdP rotates it.

ServiceAccountAuthProvider
    Resolves a Google service-account JSON secret and returns
    ``google.oauth2.service_account.Credentials`` via
    :meth:`~AuthProvider.get_client_credentials`. Used by the Google Drive
    connector; returns empty HTTP headers.

ApiKeyQueryAuthProvider
    Injects an API key as a query-string parameter via
    :meth:`~AuthProvider.get_query_params`. Returns empty HTTP headers.
"""

from .apikey_query import ApiKeyQueryAuthProvider
from .base import AuthProvider
from .no_auth import NoAuthProvider
from .oauth2 import OAuth2AuthProvider
from .service_account import ServiceAccountAuthProvider
from .static_token import StaticTokenAuthProvider

__all__ = [
    "AuthProvider",
    "NoAuthProvider",
    "StaticTokenAuthProvider",
    "OAuth2AuthProvider",
    "ServiceAccountAuthProvider",
    "ApiKeyQueryAuthProvider",
]
