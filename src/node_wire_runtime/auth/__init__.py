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
    Fetches and caches OAuth 2.0 access tokens (Client Credentials grant).
    Supports ``private_key_jwt`` (SMART Backend Services / Epic / Cerner) and
    ``client_secret_post``. Uses ``asyncio.Lock`` to prevent concurrent
    token-refresh storms.

ServiceAccountAuthProvider
    Resolves a Google service-account JSON secret and returns
    ``google.oauth2.service_account.Credentials`` via
    :meth:`~AuthProvider.get_client_credentials`. Used by the Google Drive
    connector; returns empty HTTP headers.
"""

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
]
