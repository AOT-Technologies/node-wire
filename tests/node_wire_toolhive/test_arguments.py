# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""report_call_errors: invalid tool arguments fail in node-wire's error taxonomy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from node_wire_toolhive import NodeWireClient, register_config_tools, report_call_errors

from .conftest import CONNECTOR_ID, mcp_request

_TRACE = r"\(trace_id=([0-9a-f-]{36})\)$"


def _server(node_wire_env: Path, *, report: bool = True) -> FastMCP:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    mcp = FastMCP("t")

    async def post_message(channel: str, text: str) -> dict:
        """Post ``text`` to ``channel``."""
        return {"channel": channel, "text": text}

    mcp.add_tool(post_message)
    register_config_tools(mcp, client)
    if report:
        report_call_errors(mcp, client)
    return mcp


async def test_missing_arguments_are_a_validation_error_with_a_trace_id(
    node_wire_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    mcp = _server(node_wire_env)

    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("post_message", {"channel": "C1"})

    text = str(exc.value)
    assert text.startswith("VALIDATION_ERROR [BUSINESS]: Input validation failed; text: "), text
    trace = re.search(_TRACE, text)
    assert trace, text
    (record,) = [r for r in caplog.records if getattr(r, "trace_id", None) == trace.group(1)]
    assert record.audit_event == "invocation_validation_failure"
    assert (record.connector_id, record.action) == (CONNECTOR_ID, "post_message")
    assert (record.error_code, record.error_category) == ("VALIDATION_ERROR", "BUSINESS")


async def test_every_invalid_field_is_named(node_wire_env: Path) -> None:
    mcp = _server(node_wire_env)
    with pytest.raises(ToolError, match=r"; channel: Field required; text: Field required \("):
        await mcp.call_tool("post_message", {})


async def test_valid_arguments_still_reach_the_tool(node_wire_env: Path) -> None:
    mcp = _server(node_wire_env)
    result = await mcp.call_tool("post_message", {"channel": "C1", "text": "hi"})
    content = result[0] if isinstance(result, tuple) else result
    assert '"text": "hi"' in content[0].text


async def test_config_tools_are_covered_too(node_wire_env: Path) -> None:
    mcp = _server(node_wire_env)
    with mcp_request({"x-tenant-id": "acme"}), pytest.raises(ToolError, match="VALIDATION_ERROR"):
        await mcp.call_tool("nw_select_config", {})


async def test_listing_is_unchanged(node_wire_env: Path) -> None:
    """What an MCP client sees (tools/list) is untouched."""
    wrapped = [t.model_dump() for t in await _server(node_wire_env).list_tools()]
    plain = [t.model_dump() for t in await _server(node_wire_env, report=False).list_tools()]
    assert wrapped == plain and len(wrapped) == 3


async def test_an_unknown_tool_is_coded_and_traced(
    node_wire_env: Path, caplog: pytest.LogCaptureFixture
) -> None:
    mcp = _server(node_wire_env)
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("delete_everything", {})
    text = str(exc.value)
    assert text.startswith("UNKNOWN_TOOL [BUSINESS]: Unknown tool: delete_everything"), text
    trace = re.search(_TRACE, text)
    assert trace, text
    (record,) = [r for r in caplog.records if getattr(r, "trace_id", None) == trace.group(1)]
    assert (record.audit_event, record.action) == ("invocation_rejected", "delete_everything")
