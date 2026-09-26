#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""How the MCP server builds and serves tool schemas.

* The manifest is built once per set of loaded connectors, not on every tool
  call — tool calls resolve the tool name and validate arguments against it.
* The input model's docstring is published as the tool description, so it is
  not repeated inside ``inputSchema``.
* A top-level key that belongs inside a nested object says so.
"""

from __future__ import annotations

from typing import Any

import pytest

import bindings.mcp_server.server as server_mod
from bindings.mcp_server.server import McpServer, validate_tool_arguments


@pytest.fixture
def server() -> McpServer:
    return McpServer(connector_ids=["smtp", "stripe"])


def test_manifest_is_built_once_across_listing_and_calls(
    server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = server_mod.build_manifest

    def counting(connectors: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real(connectors, **kwargs)

    monkeypatch.setattr(server_mod, "build_manifest", counting)
    name = server.list_tools()[0]["name"]
    for _ in range(3):
        server._resolve_tool_name(name)
        assert server._advertised_input_schema(name) is not None
    server.list_tools()
    assert calls == 1


def test_manifest_is_rebuilt_when_the_loaded_connectors_change(
    server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.list_tools()
    real = server._factory.list_for_protocol
    monkeypatch.setattr(
        server._factory,
        "list_for_protocol",
        lambda protocol: [c for c in real(protocol) if c.connector_id == "smtp"],
    )
    assert {t["name"].split("_", 1)[0] for t in server.list_tools()} == {"smtp"}


def test_tool_description_is_not_repeated_inside_the_input_schema(server: McpServer) -> None:
    for tool in server.list_tools():
        assert "description" not in tool["input_schema"], tool["name"]


def test_list_tools_returns_copies_callers_cannot_use_to_corrupt_the_cache(
    server: McpServer,
) -> None:
    first = server.list_tools()
    first[0]["input_schema"]["properties"].clear()
    assert server.list_tools()[0]["input_schema"]["properties"]


_NESTED: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "body": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
        },
        "limit": {"type": "integer"},
    },
}


def test_misplaced_top_level_key_names_the_object_it_belongs_in() -> None:
    msg = validate_tool_arguments({"body": {"text": "hi"}, "channel": "C1"}, _NESTED)
    assert msg is not None
    assert msg.startswith("Input validation error: $: Additional properties are not allowed")
    assert "'channel' belongs inside 'body'" in msg


def test_unknown_top_level_key_gets_no_hint() -> None:
    msg = validate_tool_arguments({"nope": 1}, _NESTED)
    assert msg is not None
    assert "belongs inside" not in msg
