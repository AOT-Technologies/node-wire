#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tool-search mode: the listing is two tools, schemas are handed out on demand.

Opt-in (``McpServer(tool_mode="search")`` or ``NW_MCP_TOOL_MODE=search``).
``tools/list`` returns ``nw_search_tools`` and ``nw_call_tool`` instead of every
connector tool, so the listing stays small however large the connector is.
Modelled on FastMCP's search transform: BM25 ranking, results carry full input
schemas, hidden tools are neither found nor callable, direct calls by name keep
working.
"""

from __future__ import annotations

from typing import Any

import pytest

import bindings.mcp_server.server as server_mod
from bindings.mcp_server.server import (
    CALL_TOOL_TOOL,
    SEARCH_TOOLS_TOOL,
    McpServer,
    advertised_tools_for_connectors,
    tool_listing_bytes,
)
from bindings.mcp_server.tool_search import Bm25Index, tokenize
from node_wire_runtime.models import ConnectorResponse

# --- BM25 ranking -------------------------------------------------------------


def test_tokenize_splits_snake_camel_and_punctuation() -> None:
    assert tokenize("chat_postMessage: Sends a message.") == [
        "chat",
        "post",
        "message",
        "sends",
        "a",
        "message",
    ]


_DOCS = {
    "slack_chat_post_message": "chat post message Sends a message to a channel channel text",
    "slack_chat_delete": "chat delete Deletes a message channel ts",
    "slack_users_list": "users list Lists all users in a workspace cursor limit",
    "slack_files_upload": "files upload Uploads or creates a file channels content",
}


def test_best_match_ranks_first() -> None:
    index = Bm25Index(_DOCS)
    assert index.search("post a message to a channel", limit=5)[0] == "slack_chat_post_message"


def test_results_are_limited() -> None:
    assert len(Bm25Index(_DOCS).search("channel", limit=2)) == 2


def test_no_matching_words_returns_nothing() -> None:
    assert Bm25Index(_DOCS).search("invoice refund", limit=5) == []


# --- Server in search mode ------------------------------------------------------


@pytest.fixture
def search_server() -> McpServer:
    return McpServer(connector_ids=["smtp", "stripe", "salesforce"], tool_mode="search")


def _names(server: McpServer) -> list[str]:
    return [t["name"] for t in server.list_tools()]


def test_search_mode_lists_only_the_two_search_tools(search_server: McpServer) -> None:
    assert _names(search_server) == [SEARCH_TOOLS_TOOL, CALL_TOOL_TOOL]


def test_list_mode_is_the_default_and_unchanged() -> None:
    names = _names(McpServer(connector_ids=["smtp"]))
    assert SEARCH_TOOLS_TOOL not in names
    assert "smtp_send_email" in names


def test_mode_can_be_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_TOOL_MODE", "search")
    assert _names(McpServer(connector_ids=["smtp"])) == [SEARCH_TOOLS_TOOL, CALL_TOOL_TOOL]


def test_unknown_mode_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="NW_MCP_TOOL_MODE"):
        McpServer(connector_ids=["smtp"], tool_mode="lazy")


def test_search_description_says_what_the_server_holds(search_server: McpServer) -> None:
    search = search_server.list_tools()[0]
    total = len(McpServer(connector_ids=["smtp", "stripe", "salesforce"]).list_tools())
    assert f"{total} tools" in search["description"]


async def test_search_returns_ranked_tools_with_full_schemas(search_server: McpServer) -> None:
    result = await search_server.invoke_tool(SEARCH_TOOLS_TOOL, {"query": "send an email"})
    first = result["tools"][0]
    assert first["name"] == "smtp_send_email"
    assert first["description"]
    assert "subject" in first["inputSchema"]["properties"]
    # The advertised form: no internal routing keys.
    assert "nw_in" not in str(first["inputSchema"])


async def test_search_limit_is_respected(search_server: McpServer) -> None:
    result = await search_server.invoke_tool(SEARCH_TOOLS_TOOL, {"query": "contact", "limit": 1})
    assert len(result["tools"]) == 1


async def test_search_with_no_match_says_so(search_server: McpServer) -> None:
    result = await search_server.invoke_tool(SEARCH_TOOLS_TOOL, {"query": "zzzqqq"})
    assert result["tools"] == []
    assert "No tools matched" in result["message"]


async def test_search_requires_a_query(search_server: McpServer) -> None:
    with pytest.raises(ValueError, match="query"):
        await search_server.invoke_tool(SEARCH_TOOLS_TOOL, {})


async def test_hidden_tools_are_neither_found_nor_callable(
    search_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = search_server._tool_visible

    def hide_smtp(entry: Any, identity: Any, **kwargs: Any) -> bool:
        return entry["connector_id"] != "smtp" and real(entry, identity, **kwargs)

    monkeypatch.setattr(search_server, "_tool_visible", hide_smtp)
    found = await search_server.invoke_tool(SEARCH_TOOLS_TOOL, {"query": "send an email"})
    assert all(not t["name"].startswith("smtp_") for t in found["tools"])
    with pytest.raises(ValueError, match="Unknown tool"):
        await search_server.invoke_tool(
            CALL_TOOL_TOOL, {"name": "smtp_send_email", "arguments": {}}
        )


def _capture_invoke(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_invoke(factory: Any, **kwargs: Any) -> ConnectorResponse:
        calls.append(kwargs)
        return ConnectorResponse(success=True, data={"sent": True}, trace_id="t")

    monkeypatch.setattr(server_mod, "invoke", fake_invoke)
    return calls


async def test_call_tool_runs_the_named_tool(
    search_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_invoke(monkeypatch)
    args = {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
    await search_server.invoke_tool(CALL_TOOL_TOOL, {"name": "smtp_send_email", "arguments": args})
    assert calls[0]["connector_id"] == "smtp"
    assert calls[0]["action"] == "send_email"
    assert calls[0]["payload"]["subject"] == "Hi"


async def test_call_tool_validates_arguments_like_a_direct_call(
    search_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_invoke(monkeypatch)
    with pytest.raises(ValueError, match=r"Input validation error: \$: 'subject' is a required"):
        await search_server.invoke_tool(
            CALL_TOOL_TOOL,
            {"name": "smtp_send_email", "arguments": {"to": ["a@example.com"], "body": "x"}},
        )
    assert calls == []


async def test_call_tool_refuses_the_search_tools_themselves(search_server: McpServer) -> None:
    with pytest.raises(ValueError, match="cannot be called through"):
        await search_server.invoke_tool(
            CALL_TOOL_TOOL, {"name": SEARCH_TOOLS_TOOL, "arguments": {"query": "x"}}
        )


async def test_direct_calls_by_name_still_work_in_search_mode(
    search_server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture_invoke(monkeypatch)
    await search_server.invoke_tool(
        "smtp_send_email", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"}
    )
    assert calls[0]["action"] == "send_email"


async def test_search_tools_are_unknown_in_list_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        await McpServer(connector_ids=["smtp"]).invoke_tool(SEARCH_TOOLS_TOOL, {"query": "x"})


# --- Measuring a listing without a running server ------------------------------


def test_listing_can_be_measured_from_connector_classes() -> None:
    from node_wire_runtime import get_connector_registry

    registry = get_connector_registry()
    tools = advertised_tools_for_connectors([registry["smtp"], registry["stripe"]])
    listed = McpServer(connector_ids=["smtp", "stripe"]).list_tools()
    assert [t["name"] for t in tools] == [t["name"] for t in listed]
    assert all(set(t) == {"name", "description", "inputSchema"} for t in tools)
    assert tool_listing_bytes(tools) > 0


async def test_measured_bytes_match_what_the_sdk_sends() -> None:
    import mcp.types as types

    from node_wire_runtime import get_connector_registry

    server = McpServer(connector_ids=["smtp", "stripe"])
    low = server._setup_lowlevel_server()
    result = await low.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list")
    )
    sent = result.root.model_dump_json(by_alias=True, exclude_none=True)
    registry = get_connector_registry()
    measured = tool_listing_bytes(
        advertised_tools_for_connectors([registry["smtp"], registry["stripe"]])
    )
    assert measured == len(sent.encode("utf-8"))


def test_search_mode_listing_is_small(search_server: McpServer) -> None:
    tools = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
        for t in search_server.list_tools()
    ]
    assert tool_listing_bytes(tools) < 2048
