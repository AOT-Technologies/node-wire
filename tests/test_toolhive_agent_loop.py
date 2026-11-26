#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""ToolHiveAgent: run() and run_events() share one ReAct loop and terminal states."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from agents.llm_factory import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall
from agents.toolhive import AgentOutcome, MultiMcpClient, ToolHiveAgent, ToolHiveMcpClient

TOOLS = [{"name": "fhir_cerner_search_patients", "description": "d", "input_schema": {}}]


class _LLM(BaseLLMProvider):
    def __init__(self, responses: List[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat_with_tools(self, messages: List[LLMMessage], tools: List[Dict[str, Any]]) -> Any:
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(resp, Exception):
            raise resp
        return resp


def _call(name: str = "fhir_cerner_search_patients") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id=str(uuid.uuid4()), name=name, arguments={})],
        stop_reason="tool_calls",
    )


def _final(text: str = "done") -> LLMResponse:
    return LLMResponse(content=text, tool_calls=[], stop_reason="stop")


def _mcp(result: Any = '{"ok": true}', *, list_error: Exception | None = None) -> AsyncMock:
    mcp = AsyncMock(spec=ToolHiveMcpClient)
    if list_error is not None:
        mcp.list_tools.side_effect = list_error
    else:
        mcp.list_tools.return_value = TOOLS
    if isinstance(result, Exception):
        mcp.call_tool.side_effect = result
    else:
        mcp.call_tool.return_value = result
    return mcp


async def _events(agent: ToolHiveAgent) -> List[Dict[str, Any]]:
    return [e async for e in agent.run_events("task")]


_PAGED = json.dumps(
    {"data": {"_server_pagination_metadata": {"next_page_token": "tok-2", "items_returned": 5}}}
)


@pytest.mark.asyncio
async def test_pagination_hint_reaches_both_paths() -> None:
    run_result = await ToolHiveAgent(_mcp(_PAGED), _LLM([_call(), _final()])).run("task")
    events = await _events(ToolHiveAgent(_mcp(_PAGED), _LLM([_call(), _final()])))
    step = next(e for e in events if e["type"] == "step")
    assert "page_token='tok-2'" in (run_result.steps[0].tool_result or "")
    assert "page_token='tok-2'" in step["result"]


@pytest.mark.asyncio
async def test_run_uses_sanitised_error_text() -> None:
    secret = "postgres://admin:hunter2@db"
    tool_err = await ToolHiveAgent(_mcp(RuntimeError(secret)), _LLM([_call(), _final()])).run("t")
    assert secret not in (tool_err.steps[0].tool_result or "")
    assert "trace_id=" in (tool_err.steps[0].tool_result or "")

    list_err = await ToolHiveAgent(_mcp(list_error=RuntimeError(secret)), _LLM([])).run("t")
    assert secret not in (list_err.error or "")
    assert "Failed to list MCP tools" in (list_err.error or "")

    llm_err = await ToolHiveAgent(_mcp(), _LLM([RuntimeError(secret)])).run("t")
    assert secret not in (llm_err.error or "")
    assert "LLM error at step 1" in (llm_err.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mcp_kwargs", "responses", "max_steps", "outcome", "success"),
    [
        ({}, [_final()], 5, AgentOutcome.FINISHED, True),
        ({"result": "ERROR: bad"}, [_call()], 5, AgentOutcome.ABORTED, False),
        ({}, [RuntimeError("x")], 5, AgentOutcome.LLM_FAILED, False),
        ({"list_error": RuntimeError("x")}, [_final()], 5, AgentOutcome.LIST_FAILED, False),
        ({}, [_call()], 2, AgentOutcome.EXHAUSTED, False),
    ],
)
async def test_terminal_outcomes_agree(mcp_kwargs, responses, max_steps, outcome, success) -> None:
    result = await ToolHiveAgent(_mcp(**mcp_kwargs), _LLM(responses), max_steps=max_steps).run("t")
    events = await _events(ToolHiveAgent(_mcp(**mcp_kwargs), _LLM(responses), max_steps=max_steps))
    assert result.outcome is outcome
    assert result.success is success
    assert events[0]["type"] == "meta"
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is success
    assert [e for e in events if e["type"] == "done"] == [events[-1]]


@pytest.mark.asyncio
async def test_unexpected_error_still_emits_done() -> None:
    events = await _events(ToolHiveAgent(_mcp(), _LLM([object()])))
    assert events[-1]["type"] == "done"
    assert events[-1]["success"] is False
    assert any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
async def test_event_order_contract() -> None:
    events = await _events(ToolHiveAgent(_mcp(), _LLM([_call(), _final("all good")])))
    order = {"meta": 0, "status": 1, "step": 1, "final_chunk": 2, "done": 3}
    ranks = [order[e["type"]] for e in events]
    assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_multi_client_broadcasts_selection_tools() -> None:
    clients = [AsyncMock(), AsyncMock(), AsyncMock()]
    for i, c in enumerate(clients):
        c.list_tools.return_value = [{"name": "nw_select_tenant"}, {"name": f"tool_{i}"}]
        c.call_tool.return_value = f'{{"selected": {i}}}'
    multi = MultiMcpClient(clients)
    await multi.list_tools()

    await multi.call_tool("nw_select_tenant", {"tenant_id": "acme"})
    await multi.call_tool("nw_select_config", {"config_name": "prod"})
    for c in clients:
        assert [call.args[0] for call in c.call_tool.await_args_list] == [
            "nw_select_tenant",
            "nw_select_config",
        ]

    await multi.call_tool("tool_1", {})
    assert clients[1].call_tool.await_count == 3
    assert clients[0].call_tool.await_count == 2


@pytest.mark.asyncio
async def test_multi_client_broadcast_reports_partial_failure() -> None:
    ok, bad = AsyncMock(), AsyncMock()
    ok.call_tool.return_value = '{"success": true}'
    bad.call_tool.side_effect = RuntimeError("down")
    multi = MultiMcpClient([ok, bad])
    with pytest.raises(RuntimeError):
        await multi.call_tool("nw_select_tenant", {"tenant_id": "acme"})
    ok.call_tool.assert_awaited_once()
