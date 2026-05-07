"""Tests for ToolHiveMcpClient HTTP transport and toolhive helper edge cases."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from agents.toolhive import (
    ToolHiveMcpClient,
    resolve_max_tool_failures,
    truncate_tool_result_for_llm,
)


def test_truncate_tool_result_non_numeric_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLHIVE_MAX_TOOL_RESULT_CHARS", "not-int")
    long = "z" * 5000
    assert truncate_tool_result_for_llm(long) == long


def test_resolve_max_tool_failures_non_numeric_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLHIVE_MAX_TOOL_FAILURES", "bad")
    assert resolve_max_tool_failures(None) == 2


def test_toolhive_mcp_client_initialize_list_tools_call_tool() -> None:
    """Exercise _initialize, tools/list, and tools/call over MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        method = body.get("method")
        req_id = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05"}},
                headers={"Mcp-Session-Id": "sess-abc"},
            )
        if method == "notifications/initialized":
            return httpx.Response(200, json={})
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": [{"name": "smtp.send_email", "description": "d"}]},
                },
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"content": [{"type": "text", "text": "sent"}]},
                },
            )
        return httpx.Response(404, json={"error": "unknown"})

    transport = httpx.MockTransport(handler)
    _RealAsyncClient = httpx.AsyncClient

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, timeout=float(kwargs.get("timeout", 60.0)))

    async def _run() -> None:
        with patch("httpx.AsyncClient", side_effect=make_client):
            client = ToolHiveMcpClient("http://127.0.0.1:9/mcp")
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "smtp.send_email"
            text = await client.call_tool(
                "smtp.send_email",
                {"to": ["a@b.com"], "subject": "s", "body": "b"},
            )
            assert text == "sent"

    asyncio.run(_run())
