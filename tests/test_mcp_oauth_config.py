from __future__ import annotations

import pytest

from node_wire_runtime.mcp_client.config import (
    AuthConfig,
    AuthRedirectConfig,
    McpClientConfig,
    McpServerConfig,
    RedirectMode,
    canonicalize_mcp_server_url,
)
from node_wire_runtime.mcp_client.exceptions import McpOAuthConfigurationError


def test_canonicalize_strips_trailing_slash() -> None:
    assert canonicalize_mcp_server_url("https://mcp.example.com/mcp/") == (
        "https://mcp.example.com/mcp"
    )


def test_mcp_client_config_requires_https_or_http() -> None:
    with pytest.raises(ValueError, match="http or https"):
        McpClientConfig(server=McpServerConfig(url="ftp://bad"))


def test_mcp_client_config_populates_aliases() -> None:
    cfg = McpClientConfig.model_validate(
        {
            "server": {"url": "https://mcp.example.com/"},
            "auth": {
                "discovery": {"cacheTtlSeconds": 120},
                "client": {"clientId": "cid", "clientSecret": "sec"},
            },
        }
    )
    assert cfg.auth.discovery.cache_ttl_seconds == 120
    assert cfg.auth.client.id == "cid"
    assert cfg.canonical_server_url == "https://mcp.example.com"


def test_production_requires_https_mcp_server() -> None:
    with pytest.raises(McpOAuthConfigurationError, match="mcp.server.url"):
        McpClientConfig(
            server=McpServerConfig(url="http://mcp.example.com/mcp"),
            auth=AuthConfig(
                production=True,
                redirect=AuthRedirectConfig(
                    mode=RedirectMode.CONFIGURED_URL,
                    url="https://app.example.com/callback",
                ),
            ),
        )


def test_production_requires_configured_url_https_redirect() -> None:
    with pytest.raises(McpOAuthConfigurationError, match="redirect.mode"):
        McpClientConfig(
            server=McpServerConfig(url="https://mcp.example.com/mcp"),
            auth=AuthConfig(production=True),
        )

    with pytest.raises(McpOAuthConfigurationError, match="redirect.url"):
        McpClientConfig(
            server=McpServerConfig(url="https://mcp.example.com/mcp"),
            auth=AuthConfig(
                production=True,
                redirect=AuthRedirectConfig(
                    mode=RedirectMode.CONFIGURED_URL,
                    url="http://127.0.0.1:8765/callback",
                ),
            ),
        )


def test_production_valid_config() -> None:
    cfg = McpClientConfig(
        server=McpServerConfig(url="https://mcp.example.com/mcp"),
        auth=AuthConfig(
            production=True,
            redirect=AuthRedirectConfig(
                mode=RedirectMode.CONFIGURED_URL,
                url="https://app.example.com/oauth/callback",
            ),
        ),
    )
    assert cfg.auth.production is True
    assert cfg.auth.redirect.mode == RedirectMode.CONFIGURED_URL


def test_config_from_env_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_runtime.mcp_client.env_config import config_from_env

    monkeypatch.setenv("NW_MCP_OAUTH_PRODUCTION", "true")
    monkeypatch.setenv("NW_MCP_OAUTH_REDIRECT_URL", "https://app.example.com/callback")
    cfg = config_from_env(server_url="https://mcp.example.com/mcp")
    assert cfg.auth.production is True
    assert cfg.auth.redirect.mode == RedirectMode.CONFIGURED_URL
