#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
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


def test_toolhive_mcp_client_initialize_survives_notification_transport_error() -> None:
    """The initialized notification is fire-and-forget; transport errors are swallowed."""

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
            raise httpx.ReadTimeout("notification timed out", request=request)
        return httpx.Response(404, json={"error": "unknown"})

    transport = httpx.MockTransport(handler)
    _RealAsyncClient = httpx.AsyncClient

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=transport, timeout=float(kwargs.get("timeout", 60.0)))

    async def _run() -> None:
        with patch("httpx.AsyncClient", side_effect=make_client):
            client = ToolHiveMcpClient("http://127.0.0.1:9/mcp")
            await client._initialize()
            assert client._initialized is True
            assert client._session_id == "sess-abc"

    asyncio.run(_run())


def test_toolhive_mcp_client_reuses_one_connection_pool() -> None:
    """Handshake + RPCs share one AsyncClient; aclose releases it."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        result = {"tools": []} if body.get("method") == "tools/list" else {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})

    transport = httpx.MockTransport(handler)
    _RealAsyncClient = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        c = _RealAsyncClient(transport=transport, timeout=float(kwargs.get("timeout", 60.0)))
        created.append(c)
        return c

    async def _run() -> None:
        with patch("httpx.AsyncClient", side_effect=make_client):
            async with ToolHiveMcpClient("http://127.0.0.1:9/mcp") as client:
                await client.list_tools()
                await client.list_tools()
        assert len(created) == 1
        assert created[0].is_closed

    asyncio.run(_run())


def test_multi_mcp_client_lists_concurrently_and_keeps_first_writer() -> None:
    from agents.toolhive import MultiMcpClient

    class _Fake:
        def __init__(self, tools, delay: float, fail: bool = False) -> None:
            self._tools, self._delay, self._fail = tools, delay, fail

        async def list_tools(self):
            await asyncio.sleep(self._delay)
            if self._fail:
                raise RuntimeError("down")
            return self._tools

        async def call_tool(self, name, arguments):  # pragma: no cover - unused
            return ""

    async def _run() -> None:
        multi = MultiMcpClient(
            [
                _Fake([{"name": "a", "src": 0}], 0.05),
                _Fake([], 0.0, fail=True),
                _Fake([{"name": "a", "src": 2}, {"name": "b", "src": 2}], 0.0),
            ]
        )
        tools = await multi.list_tools()
        assert [(t["name"], t["src"]) for t in tools] == [("a", 0), ("b", 2)]

    asyncio.run(_run())
