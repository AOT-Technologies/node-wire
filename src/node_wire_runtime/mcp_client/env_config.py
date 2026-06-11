#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Build :class:`McpClientConfig` from environment variables."""

from __future__ import annotations

import os

from .config import (
    AuthClientConfig,
    AuthConfig,
    AuthDiscoveryConfig,
    AuthDcrConfig,
    AuthRedirectConfig,
    AuthTokenConfig,
    McpClientConfig,
    McpServerConfig,
    RedirectMode,
    TokenStoreMode,
)


def _truthy(val: str | None) -> bool:
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def mcp_oauth_enabled() -> bool:
    return _truthy(os.environ.get("NW_MCP_OAUTH_ENABLED"))


def legacy_static_mcp_token() -> str | None:
    return (
        os.environ.get("TOOLHIVE_MCP_BEARER_TOKEN") or os.environ.get("TOOLHIVE_MCP_API_KEY") or ""
    ).strip() or None


def mcp_oauth_user_id() -> str:
    return (os.environ.get("NW_MCP_OAUTH_USER_ID") or "default-user").strip()


def config_from_env(*, server_url: str | None = None) -> McpClientConfig:
    """
    Resolve MCP OAuth client config from env.

    ``server_url`` defaults to ``NW_MCP_SERVER_URL`` or the provided HTTP MCP base URL.
    """
    url = (server_url or os.environ.get("NW_MCP_SERVER_URL") or "").strip()
    if not url:
        raise ValueError("MCP server URL required (argument or NW_MCP_SERVER_URL)")

    production = _truthy(os.environ.get("NW_MCP_OAUTH_PRODUCTION"))
    default_redirect_mode = "configured-url" if production else "loopback"
    redirect_mode_raw = (
        (os.environ.get("NW_MCP_OAUTH_REDIRECT_MODE") or default_redirect_mode).strip().lower()
    )
    redirect_mode = (
        RedirectMode.CONFIGURED_URL
        if redirect_mode_raw == "configured-url"
        else RedirectMode.LOOPBACK
    )
    token_store_raw = (os.environ.get("NW_MCP_OAUTH_TOKEN_STORE") or "os-keychain").strip().lower()
    token_store = (
        TokenStoreMode.CONFIGURED_SECRET_STORE
        if token_store_raw == "configured-secret-store"
        else TokenStoreMode.OS_KEYCHAIN
    )

    return McpClientConfig(
        server=McpServerConfig(url=url),
        auth=AuthConfig(
            production=production,
            scopes=(os.environ.get("NW_MCP_OAUTH_SCOPES") or "").strip(),
            client=AuthClientConfig(
                clientId=(os.environ.get("NW_MCP_OAUTH_CLIENT_ID") or "").strip(),
                clientSecret=(os.environ.get("NW_MCP_OAUTH_CLIENT_SECRET") or "").strip(),
            ),
            redirect=AuthRedirectConfig(
                mode=redirect_mode,
                url=(os.environ.get("NW_MCP_OAUTH_REDIRECT_URL") or "http://127.0.0.1:0/callback"),
                loopbackHost=(os.environ.get("NW_MCP_OAUTH_LOOPBACK_HOST") or "127.0.0.1"),
                loopbackPath=(os.environ.get("NW_MCP_OAUTH_LOOPBACK_PATH") or "/callback"),
            ),
            discovery=AuthDiscoveryConfig(
                cacheTtlSeconds=int(os.environ.get("NW_MCP_OAUTH_DISCOVERY_TTL", "3600")),
            ),
            dcr=AuthDcrConfig(enabled=not _truthy(os.environ.get("NW_MCP_OAUTH_DCR_DISABLED"))),
            token=AuthTokenConfig(
                refreshLeadSeconds=int(os.environ.get("NW_MCP_OAUTH_REFRESH_LEAD", "60")),
                store=token_store,
            ),
            registrationStorePath=(os.environ.get("NW_MCP_OAUTH_REGISTRATION_PATH") or None),
            issuerOverride=(os.environ.get("NW_MCP_OAUTH_ISSUER_OVERRIDE") or None),
        ),
    )
