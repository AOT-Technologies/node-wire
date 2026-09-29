#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Schema violations must say *where* the offending value is.

The MCP SDK reports only ``exc.message``. On a tool with a nested object,
"'channel' is a required property" then does not say whether ``channel`` is
missing from the arguments or from the nested object; the path does.
"""

from __future__ import annotations

from typing import Any

from bindings.mcp_server.server import format_tool_validation_error, validate_tool_arguments

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "body": {
            "type": "object",
            "additionalProperties": True,
            "required": ["channel"],
            "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
        }
    },
}


def test_missing_nested_field_names_the_parent_object() -> None:
    msg = validate_tool_arguments({"body": {"text": "hi"}}, SCHEMA)
    assert msg == "Input validation error: $.body: 'channel' is a required property"


def test_unexpected_top_level_field_names_the_root() -> None:
    msg = validate_tool_arguments({"body": {"text": "hi"}, "channel": "C1"}, SCHEMA)
    assert msg is not None
    assert msg.startswith("Input validation error: $: Additional properties are not allowed")


def test_nested_and_top_level_failures_report_different_paths() -> None:
    """A field missing from `body` and an unexpected top-level key are told apart by path."""
    missing = validate_tool_arguments({"body": {"text": "hi"}}, SCHEMA)
    unexpected = validate_tool_arguments({"body": {"text": "hi"}, "channel": "C1"}, SCHEMA)
    assert missing is not None and unexpected is not None
    assert "$.body" in missing and "$:" in unexpected


def test_valid_arguments_produce_no_error() -> None:
    assert validate_tool_arguments({"body": {"channel": "C1", "text": "hi"}}, SCHEMA) is None


def test_wrong_type_names_the_field() -> None:
    msg = validate_tool_arguments({"body": {"channel": 17}}, SCHEMA)
    assert msg is not None and "$.body.channel" in msg


def test_prefix_is_preserved_for_clients_matching_on_it() -> None:
    msg = validate_tool_arguments({"nope": 1}, SCHEMA)
    assert msg is not None and msg.startswith("Input validation error: ")


def test_non_dict_schema_is_skipped() -> None:
    assert validate_tool_arguments({"anything": 1}, None) is None


def test_formatter_falls_back_to_root_when_no_path() -> None:
    class _Exc:
        message = "something went wrong"
        json_path = None

    assert format_tool_validation_error(_Exc()) == (
        "Input validation error: $: something went wrong"
    )
