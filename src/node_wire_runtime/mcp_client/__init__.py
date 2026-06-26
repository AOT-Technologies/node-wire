#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Outbound MCP OAuth 2.1 client (MCP Authorization spec 2025-11-25)."""

from .challenges import WwwAuthenticateChallenge, parse_www_authenticate
from .client import McpOAuthClient, create_http_mcp_client, create_http_mcp_clients_for_urls
from .config import (
    AuthConfig,
    AuthClientConfig,
    AuthDcrConfig,
    AuthDiscoveryConfig,
    AuthRedirectConfig,
    AuthTokenConfig,
    McpClientConfig,
    McpServerConfig,
    RedirectMode,
    TokenStoreMode,
    canonicalize_mcp_server_url,
)
from .discovery import (
    AuthorizationServerMetadata,
    DiscoveryCache,
    DiscoveryResult,
    ProtectedResourceMetadata,
    discover,
    discovery_cache_for_config,
    fetch_authorization_server_metadata,
    fetch_protected_resource_metadata,
    parse_resource_metadata_url,
)
from .dcr import register_dynamic_client, resolve_client_registration
from .env_config import (
    config_from_env,
    legacy_static_mcp_token,
    mcp_oauth_enabled,
    mcp_oauth_user_id,
)
from .exceptions import (
    McpAudienceMismatch,
    McpOAuthConfigurationError,
    McpOAuthDiscoveryError,
    McpOAuthError,
    McpOAuthFlowAborted,
    McpOAuthRegistrationError,
    McpOAuthSecurityError,
    McpTokenRefreshError,
)
from .oauth_flow import (
    AuthorizationCodeFlow,
    AuthorizationSession,
    OAuthTokenSet,
    PkcePair,
    build_authorization_url,
    generate_pkce_pair,
    generate_state,
)
from .redirect_listener import (
    AuthorizationCallback,
    LoopbackRedirectBinding,
    LoopbackRedirectListener,
)
from .storage import ClientRegistration, RegistrationStore, default_registration_store_dir
from .token_manager import TokenManager
from .token_storage import (
    InMemoryTokenStore,
    StoredOAuthTokens,
    TokenStore,
    make_token_store,
    stored_from_oauth_response,
    token_partition_key,
)

__all__ = [
    "WwwAuthenticateChallenge",
    "parse_www_authenticate",
    "McpOAuthClient",
    "create_http_mcp_client",
    "create_http_mcp_clients_for_urls",
    "AuthConfig",
    "AuthClientConfig",
    "AuthDcrConfig",
    "AuthDiscoveryConfig",
    "AuthRedirectConfig",
    "AuthTokenConfig",
    "McpClientConfig",
    "McpServerConfig",
    "RedirectMode",
    "TokenStoreMode",
    "canonicalize_mcp_server_url",
    "AuthorizationServerMetadata",
    "DiscoveryCache",
    "DiscoveryResult",
    "ProtectedResourceMetadata",
    "discover",
    "discovery_cache_for_config",
    "fetch_authorization_server_metadata",
    "fetch_protected_resource_metadata",
    "parse_resource_metadata_url",
    "register_dynamic_client",
    "resolve_client_registration",
    "config_from_env",
    "legacy_static_mcp_token",
    "mcp_oauth_enabled",
    "mcp_oauth_user_id",
    "McpAudienceMismatch",
    "McpOAuthConfigurationError",
    "McpOAuthDiscoveryError",
    "McpOAuthError",
    "McpOAuthFlowAborted",
    "McpOAuthRegistrationError",
    "McpOAuthSecurityError",
    "McpTokenRefreshError",
    "AuthorizationCodeFlow",
    "AuthorizationSession",
    "OAuthTokenSet",
    "PkcePair",
    "build_authorization_url",
    "generate_pkce_pair",
    "generate_state",
    "AuthorizationCallback",
    "LoopbackRedirectBinding",
    "LoopbackRedirectListener",
    "ClientRegistration",
    "RegistrationStore",
    "default_registration_store_dir",
    "TokenManager",
    "InMemoryTokenStore",
    "StoredOAuthTokens",
    "TokenStore",
    "make_token_store",
    "stored_from_oauth_response",
    "token_partition_key",
]
