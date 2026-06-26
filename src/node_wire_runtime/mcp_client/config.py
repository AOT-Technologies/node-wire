#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Configuration for outbound MCP OAuth client (requirements Section 10)."""

from __future__ import annotations

from enum import Enum
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RedirectMode(str, Enum):
    LOOPBACK = "loopback"
    CONFIGURED_URL = "configured-url"


class TokenStoreMode(str, Enum):
    OS_KEYCHAIN = "os-keychain"
    CONFIGURED_SECRET_STORE = "configured-secret-store"


class AuthDiscoveryConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cache_ttl_seconds: int = Field(
        default=3600,
        alias="cacheTtlSeconds",
        description="How long discovery metadata is cached before re-fetch.",
    )


class AuthDcrConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    client_name: str = Field(default="node-wire MCP Client", alias="clientName")


class AuthClientConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="", alias="clientId")
    secret: str = Field(default="", alias="clientSecret")


class AuthRedirectConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    mode: RedirectMode = RedirectMode.LOOPBACK
    url: str = Field(
        default="http://127.0.0.1:0/callback",
        description="Used when mode=configured-url; loopback ignores port 0 at runtime.",
    )
    loopback_host: str = Field(default="127.0.0.1", alias="loopbackHost")
    loopback_path: str = Field(default="/callback", alias="loopbackPath")


class AuthTokenConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    refresh_lead_seconds: int = Field(default=60, alias="refreshLeadSeconds")
    store: TokenStoreMode = TokenStoreMode.OS_KEYCHAIN


class AuthConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    production: bool = Field(
        default=False,
        description=(
            "When true, require configured-url HTTPS redirect and https:// MCP server URL; "
            "HTTP loopback is disabled."
        ),
    )
    discovery: AuthDiscoveryConfig = Field(default_factory=AuthDiscoveryConfig)
    dcr: AuthDcrConfig = Field(default_factory=AuthDcrConfig)
    client: AuthClientConfig = Field(default_factory=AuthClientConfig)
    scopes: str = ""
    redirect: AuthRedirectConfig = Field(default_factory=AuthRedirectConfig)
    token: AuthTokenConfig = Field(default_factory=AuthTokenConfig)
    issuer_override: Optional[str] = Field(
        default=None,
        alias="issuerOverride",
        description="Force a specific authorization server issuer from PRM list.",
    )
    registration_store_path: Optional[str] = Field(
        default=None,
        alias="registrationStorePath",
        description="Directory for persisted DCR client registrations per issuer.",
    )


class McpServerConfig(BaseModel):
    url: str = Field(description="Canonical URL of the remote MCP server (resource indicator).")

    @field_validator("url")
    @classmethod
    def _must_be_http_url(cls, v: str) -> str:
        parsed = urlsplit(v.strip())
        if parsed.scheme not in ("https", "http"):
            raise ValueError("mcp.server.url must use http or https")
        if not parsed.netloc:
            raise ValueError("mcp.server.url must include a host")
        return v.strip()


class McpClientConfig(BaseModel):
    """
    Operator configuration per remote MCP server connection.

    Maps to requirements document Section 10.
    """

    model_config = ConfigDict(populate_by_name=True)

    server: McpServerConfig
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @model_validator(mode="after")
    def _validate_production_hardening(self) -> McpClientConfig:
        validate_production_hardening(self)
        return self

    @property
    def canonical_server_url(self) -> str:
        """Normalized resource indicator (no fragment, no trailing slash on path root)."""
        return canonicalize_mcp_server_url(self.server.url)


def validate_production_hardening(config: McpClientConfig) -> None:
    """
    Enforce production OAuth profile: HTTPS MCP server, configured-url redirect only.

    HTTP loopback remains available when ``auth.production`` is false (default).
    """
    if not config.auth.production:
        return

    from .exceptions import McpOAuthConfigurationError

    if urlsplit(config.server.url).scheme != "https":
        raise McpOAuthConfigurationError("auth.production requires mcp.server.url to use https://")
    if config.auth.redirect.mode != RedirectMode.CONFIGURED_URL:
        raise McpOAuthConfigurationError(
            "auth.production requires auth.redirect.mode=configured-url "
            "(HTTP loopback is disabled in production)"
        )
    redirect_url = config.auth.redirect.url.strip()
    if not redirect_url.startswith("https://"):
        raise McpOAuthConfigurationError(
            "auth.production requires auth.redirect.url to use https://"
        )


def canonicalize_mcp_server_url(url: str) -> str:
    """Canonical MCP server URL for RFC 8707 ``resource`` parameter."""
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/") or ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
