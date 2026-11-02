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
from bindings.mcp_server.server import McpServer, build_manifest, validate_tool_arguments


@pytest.fixture
def server() -> McpServer:
    return McpServer(connector_ids=["smtp", "stripe"])


def test_manifest_is_built_once_across_listing_and_calls(
    server: McpServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = build_manifest

    def counting(connectors: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real(connectors, **kwargs)

    monkeypatch.setattr("bindings.mcp_server.server.build_manifest", counting)
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


def _reference_safe_schema(schema: Any) -> Any:
    """The pre-cache implementation (deepcopy + strip) to compare against."""
    import copy

    return server_mod._strip_json_schema_nulls(
        server_mod._strip_internal_keys(copy.deepcopy(schema))
    )


@pytest.mark.parametrize("multitenancy", [False, True])
def test_advertised_schemas_match_uncached_rewrite(
    server: McpServer, monkeypatch: pytest.MonkeyPatch, multitenancy: bool
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true" if multitenancy else "false")
    advertised = {t["name"]: t["input_schema"] for t in server._list_tools_impl(advertised=True)}
    raw = {t["name"]: t["input_schema"] for t in server._list_tools_impl()}
    assert advertised.keys() == raw.keys()
    for name, schema in raw.items():
        assert advertised[name] == _reference_safe_schema(schema), name
        if name in dict(server._manifest()[1]):
            assert server._advertised_input_schema(name) == advertised[name]


def test_safe_schema_rewrite_does_not_mutate_input() -> None:
    import copy

    schema = {
        "type": "object",
        "properties": {
            "a": {"type": ["string", "null"], "default": None, "nw_location": "query"},
            "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    }
    before = copy.deepcopy(schema)
    server_mod.mcp_llm_safe_input_schema(schema)
    assert schema == before


def test_cached_validator_matches_jsonschema_validate(server: McpServer) -> None:
    for name in list(server._manifest()[1])[:20]:
        found = server._advertised_schema_and_validator(name)
        if found is None:
            continue
        schema, validator = found
        for args in ({}, {"unexpected": 1}, {"to": 5}, {"body": {"x": None}}):
            assert validate_tool_arguments(args, schema, validator=validator) == (
                validate_tool_arguments(args, schema)
            ), (name, args)


def test_invalid_schema_still_raises_each_call() -> None:
    import jsonschema

    bad = {"type": 12}
    assert server_mod.compile_tool_validator(bad) is None
    for _ in range(2):
        with pytest.raises(jsonschema.SchemaError):
            validate_tool_arguments({}, bad, validator=server_mod.compile_tool_validator(bad))


def test_resolve_tool_name_matches_manifest_scan(server: McpServer) -> None:
    """Index lookup must return what the old linear manifest scan returned."""
    manifest, _ = server._manifest()
    for entry in manifest:
        cid, action = str(entry["connector_id"]), str(entry["action"])
        name = server_mod.mcp_advertised_tool_name(cid, action)
        allowed = server._connector_ids
        expected = [
            (str(e["connector_id"]), str(e["action"]))
            for e in manifest
            if (allowed is None or str(e["connector_id"]) in allowed)
            and server_mod.mcp_advertised_tool_name(str(e["connector_id"]), str(e["action"]))
            == name
        ]
        if len(expected) == 1:
            assert server._resolve_tool_name(name) == expected[0]
        elif not expected:
            with pytest.raises(server_mod.NodeWireError):
                server._resolve_tool_name(name)
    with pytest.raises(server_mod.NodeWireError):
        server._resolve_tool_name("no_such_tool_anywhere")
