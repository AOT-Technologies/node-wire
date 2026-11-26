# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``tools/list`` builds each Tool once; the SDK's in-call cache refresh reuses them.

A listing used to rebuild every name, description and Tool model, and compile one
argument validator per tool it never used: ~1.1 s for the first listing of a
1,239-tool server and ~6 ms for each one after. The MCP SDK also calls the handler
with ``None`` inside a tools/call for any tool name it has not seen listed.
"""

from __future__ import annotations

from typing import Any

import mcp.types as types
import pytest

import bindings.mcp_server.server as server_mod


def _list_request() -> types.ListToolsRequest:
    return types.ListToolsRequest(method="tools/list")


@pytest.fixture
def open_server(monkeypatch: pytest.MonkeyPatch) -> server_mod.McpServer:
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp,stripe")
    return server_mod.McpServer(connector_ids=["smtp", "stripe"])


async def test_listings_reuse_the_same_tool_objects(open_server: server_mod.McpServer) -> None:
    handler = open_server._setup_lowlevel_server().request_handlers[types.ListToolsRequest]
    first = (await handler(_list_request())).root.tools
    second = (await handler(_list_request())).root.tools
    assert first and all(a is b for a, b in zip(first, second, strict=True))


async def test_listing_compiles_no_validators(
    open_server: server_mod.McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _compile(_schema: Any) -> None:
        raise AssertionError("tools/list should not compile argument validators")

    monkeypatch.setattr(server_mod, "compile_tool_validator", _compile)
    handler = open_server._setup_lowlevel_server().request_handlers[types.ListToolsRequest]
    assert (await handler(_list_request())).root.tools


async def test_validator_is_compiled_for_a_call_after_a_listing(
    open_server: server_mod.McpServer,
) -> None:
    handler = open_server._setup_lowlevel_server().request_handlers[types.ListToolsRequest]
    name = (await handler(_list_request())).root.tools[0].name
    found = open_server._advertised_schema_and_validator(name)
    assert found is not None and found[1] is not None


async def test_multitenancy_change_rebuilds_the_tools(
    open_server: server_mod.McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = open_server._setup_lowlevel_server().request_handlers[types.ListToolsRequest]
    single = [t.name for t in (await handler(_list_request())).root.tools]
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    multi = [t.name for t in (await handler(_list_request())).root.tools]
    assert multi[: len(multi) - len(single)] == [
        server_mod.LIST_TENANTS_TOOL,
        server_mod.SELECT_TENANT_TOOL,
        server_mod.LIST_CONFIGS_TOOL,
        server_mod.SELECT_CONFIG_TOOL,
    ]
    assert multi[len(multi) - len(single) :] == single


async def test_sdk_cache_refresh_gets_every_tool_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh never reaches a client; handle_call_tool still checks auth and scope."""
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "the-key")
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp")
    server = server_mod.McpServer(connector_ids=["smtp"])
    low = server._setup_lowlevel_server()
    await low.request_handlers[types.ListToolsRequest](None)
    assert sorted(low._tool_cache) == sorted(server._manifest()[1])
