# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""nw_list_configs / nw_select_config on a FastMCP server."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

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


_UUID = r"[0-9a-f-]{36}"


@pytest.mark.parametrize(
    ("tool", "arguments", "headers", "error"),
    [
        (
            "nw_select_config",
            {"config_name": "staging"},
            {"x-tenant-id": "acme"},
            "CONFIG_NOT_FOUND [AUTH]",
        ),
        ("nw_list_configs", {}, {}, "MISSING_TENANT [AUTH]"),
    ],
)
async def test_config_tool_failures_carry_a_trace_id(
    node_wire_env: Path,
    caplog: pytest.LogCaptureFixture,
    tool: str,
    arguments: dict,
    headers: dict,
    error: str,
) -> None:
    """Like a connector tool: the client can quote the trace_id the server logged it under."""
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    mcp = FastMCP("t")
    register_config_tools(mcp, client)

    with mcp_request({**headers, "mcp-session-id": "s1"}), pytest.raises(ToolError) as exc:
        await mcp.call_tool(tool, arguments)

    match = re.search(rf"{re.escape(error)}: .* \(trace_id=({_UUID})\)$", str(exc.value))
    assert match, str(exc.value)
    logged = [r for r in caplog.records if getattr(r, "trace_id", None) == match.group(1)]
    assert logged and logged[0].action == tool
