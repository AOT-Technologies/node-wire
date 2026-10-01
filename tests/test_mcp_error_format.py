# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""A failed tool call on the node-wire MCP host: ``isError``, one coded line, the envelope.

The same shape as a stacklok-built server's (``node_wire_toolhive``): the runtime picks the
code, category and trace id; the binding only turns them into an MCP result.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import mcp.types as types
import pytest

import bindings.mcp_server.server as server_mod
from bindings.mcp_server.server import McpServer
from node_wire_runtime.models import ConnectorResponse, ErrorCategory

_LINE = re.compile(
    r"^(?P<code>[A-Z_]+) \[(?P<category>[A-Z]+)\]: (?P<message>.*) \(trace_id=(?P<trace>[^)]+)\)$",
    re.S,
)


@pytest.fixture
def open_server(monkeypatch: pytest.MonkeyPatch) -> McpServer:
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp")
    return McpServer(connector_ids=["smtp"])


async def _call(server: McpServer, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    low = server._setup_lowlevel_server()
    result = await low.request_handlers[types.CallToolRequest](
        types.CallToolRequest(
            method="tools/call", params=types.CallToolRequestParams(name=name, arguments=arguments)
        )
    )
    return result.root


def _failure(result: types.CallToolResult) -> re.Match[str]:
    assert result.isError is True
    line = _LINE.match(result.content[0].text)
    assert line, result.content[0].text
    envelope = result.structuredContent
    assert envelope is not None and envelope["success"] is False
    assert (envelope["error_code"], envelope["error_category"], envelope["trace_id"]) == (
        line["code"],
        line["category"],
        line["trace"],
    )
    return line


async def test_unknown_tool(open_server: McpServer, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        line = _failure(await _call(open_server, "smtp_delete_everything", {}))
    assert (line["code"], line["category"]) == ("UNKNOWN_TOOL", "BUSINESS")
    (record,) = [r for r in caplog.records if getattr(r, "trace_id", None) == line["trace"]]
    assert (record.audit_event, record.action) == ("invocation_rejected", "smtp_delete_everything")


async def test_invalid_arguments(open_server: McpServer) -> None:
    line = _failure(await _call(open_server, "smtp_send_email", {"to": "a@example.test"}))
    assert (line["code"], line["category"]) == ("VALIDATION_ERROR", "BUSINESS")
    assert line["message"].startswith("Input validation error: $: 'subject' is a required property")


async def test_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "the-key")  # auth configured, but the call sends none
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp")
    line = _failure(await _call(McpServer(connector_ids=["smtp"]), "smtp_send_email", {}))
    assert (line["code"], line["category"]) == ("MCP_AUTH_REQUIRED", "AUTH")


async def test_a_failed_connector_run(
    open_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*_: Any, **__: Any) -> ConnectorResponse:
        return ConnectorResponse(
            success=False,
            error_code="SECRET_NOT_FOUND",
            error_category=ErrorCategory.FATAL,
            message="Secret 'SMTP_PASSWORD' not found",
            trace_id="run-trace-1",
        )

    monkeypatch.setattr(server_mod, "invoke", failing)
    args = {"to": "a@example.test", "subject": "s", "body": "b"}
    line = _failure(await _call(open_server, "smtp_send_email", args))
    assert (line["code"], line["category"], line["trace"]) == (
        "SECRET_NOT_FOUND",
        "FATAL",
        "run-trace-1",  # the run's own trace id, as the runtime logged it
    )


async def test_a_successful_run_is_not_an_error(
    open_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(*_: Any, **__: Any) -> ConnectorResponse:
        return ConnectorResponse(success=True, data={"sent": True}, trace_id="run-trace-2")

    monkeypatch.setattr(server_mod, "invoke", ok)
    args = {"to": "a@example.test", "subject": "s", "body": "b"}
    result = await _call(open_server, "smtp_send_email", args)
    assert result.isError is False
    assert result.structuredContent["success"] is True


async def test_a_clients_tools_list_without_credentials_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the SDK's own cache refresh (request None) lists nothing instead of failing."""
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "the-key")
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "smtp")
    low = McpServer(connector_ids=["smtp"])._setup_lowlevel_server()
    with pytest.raises(RuntimeError, match="MCP_AUTH_REQUIRED"):
        await low.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest(method="tools/list")
        )
