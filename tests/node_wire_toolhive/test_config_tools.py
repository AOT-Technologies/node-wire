# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""nw_list_configs / nw_select_config on a FastMCP server."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from node_wire_toolhive import NodeWireClient, register_config_tools

from .conftest import CONNECTOR_ID, mcp_request


def _payload(result) -> dict:  # noqa: ANN001
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


async def test_tools_are_registered_only_with_multitenancy(
    node_wire_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)

    with_mt = FastMCP("t")
    register_config_tools(with_mt, client)
    assert {t.name for t in await with_mt.list_tools()} == {"nw_list_configs", "nw_select_config"}

    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    without_mt = FastMCP("t")
    register_config_tools(without_mt, client)
    assert await without_mt.list_tools() == []


async def test_list_then_select_config_for_the_session(node_wire_env: Path) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    mcp = FastMCP("t")
    register_config_tools(mcp, client)
    headers = {"x-tenant-id": "acme", "mcp-session-id": "s1"}

    with mcp_request(headers):
        listed = _payload(await mcp.call_tool("nw_list_configs", {}))
        selected = _payload(await mcp.call_tool("nw_select_config", {"config_name": "eu"}))
        after = _payload(await mcp.call_tool("nw_list_configs", {}))

    assert listed["tenant_id"] == "acme"
    assert listed["selected"] is None
    assert [c["name"] for c in listed["configs"]] == ["default", "eu"]
    assert selected == {"tenant_id": "acme", "selected": "eu"}
    assert after["selected"] == "eu"
