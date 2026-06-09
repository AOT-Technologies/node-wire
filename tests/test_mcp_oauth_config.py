from __future__ import annotations

import pytest

from node_wire_runtime.mcp_client.config import (
    McpClientConfig,
    McpServerConfig,
    canonicalize_mcp_server_url,
)


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
