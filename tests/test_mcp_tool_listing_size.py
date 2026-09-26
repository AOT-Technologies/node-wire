#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""``tools/list`` carries only what a caller can act on.

Measured on a 174-tool generated connector, ~170 characters appended to every
tool description (``Requires Auth: Yes``, an out-of-date ``action`` warning, the
manifest contract version) were a quarter of the payload and told a model
nothing. The contract version is still published, once, in the server's
instructions.

Schema ``title``s are removed by codegen, in the generated models themselves
(``tests/nw_connector_builder/test_codegen_titles.py``); the binding passes
schemas through, so hand-written connectors keep theirs.
"""

from __future__ import annotations

from typing import Any

import pytest

from bindings.mcp_server.server import McpServer, _tool_description, mcp_llm_safe_input_schema
from node_wire_runtime.manifest import MCP_MANIFEST_CONTRACT_VERSION


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "connector_id": "slack_web",
        "action": "chat_post_message",
        "input_schema": {"type": "object", "description": "Sends a message to a channel."},
        "requires_auth": True,
        "scopes": [],
        "rate_limit": {},
        "deprecated": False,
    }
    entry.update(overrides)
    return entry


def test_description_is_the_operation_text_alone() -> None:
    assert _tool_description(_entry()) == "Sends a message to a channel."


def test_requires_auth_is_not_advertised() -> None:
    assert "Requires Auth" not in _tool_description(_entry(requires_auth=True))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"deprecated": True}, "Deprecated."),
        ({"scopes": ["chat:write", "chat:write.public"]}, "Scopes: chat:write, chat:write.public."),
        ({"rate_limit": {"per_minute": 50}}, "Rate limit: {'per_minute': 50}."),
    ],
)
def test_actionable_notes_are_kept(overrides: dict[str, Any], expected: str) -> None:
    assert _tool_description(_entry(**overrides)) == f"Sends a message to a channel.\n\n{expected}"


def test_tool_without_operation_text_has_an_empty_description() -> None:
    assert _tool_description(_entry(input_schema={"type": "object"})) == ""


def test_contract_version_is_published_once_in_server_instructions() -> None:
    low = McpServer(connector_ids=["smtp"])._setup_lowlevel_server()
    assert f"Manifest contract v{MCP_MANIFEST_CONTRACT_VERSION}" in (low.instructions or "")


def test_listed_tools_carry_no_boilerplate() -> None:
    for tool in McpServer(connector_ids=["smtp", "stripe"]).list_tools():
        assert "Manifest contract" not in tool["description"], tool["name"]
        assert "Pass fields from inputSchema" not in tool["description"], tool["name"]


def test_binding_passes_titles_through() -> None:
    """Title removal is codegen's job (generated schema.py); hand-written schemas are untouched."""
    schema = {
        "title": "PingInput",
        "type": "object",
        "properties": {"ts": {"title": "Message timestamp", "type": "string"}},
    }
    advertised = mcp_llm_safe_input_schema(schema)
    assert advertised["title"] == "PingInput"
    assert advertised["properties"]["ts"]["title"] == "Message timestamp"
