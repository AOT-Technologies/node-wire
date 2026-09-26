# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated tools carry the spec's operation and parameter descriptions.

Without them an MCP client sees a list of tool names and argument names and
nothing else — a model has to guess what ``slack_web_chat_post_message`` does
and what ``channel`` expects. The manifest already publishes an input model's
docstring as the tool description, so the generator only has to emit it.
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any

import pytest

from nw_connector_builder.codegen import _operation_description, generate_schema_module
from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import (
    ActionPlan,
    DeriveResult,
    ParamPlan,
    derive_operations,
)
from node_wire_runtime.manifest import _schema_for


def _param(name: str, description: str | None = None, *, required: bool = False) -> ParamPlan:
    return ParamPlan(
        field_name=name,
        wire_name=name,
        location="query",
        required=required,
        schema={"type": "string"},
        python_type_hint="str",
        description=description,
    )


def _action(operation: dict[str, Any], params: list[ParamPlan] | None = None) -> ActionPlan:
    return ActionPlan(
        name="chat_post_message",
        method="POST",
        path="/chat.postMessage",
        operation=operation,
        params=params or [],
        body_schema=None,
        body_media_type=None,
        output_schema=None,
        use_rest_response_output=True,
        auth=False,
    )


def _result(action: ActionPlan) -> DeriveResult:
    return DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://slack.com/api",
        coverage_warning=False,
        total_operations=1,
    )


def _input_schema(action: ActionPlan) -> dict[str, Any]:
    """Import the generated schema module and return the action's published input schema."""
    src = generate_schema_module("demo", _result(action))
    name = f"_nw_generated_{uuid.uuid4().hex}"
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(src, name, "exec"), module.__dict__)  # noqa: S102 — generated test fixture
        return _schema_for(module.ChatPostMessageInput)
    finally:
        del sys.modules[name]


def test_summary_becomes_the_tool_description() -> None:
    schema = _input_schema(_action({"summary": "Sends a message to a channel."}))
    assert schema["description"] == "Sends a message to a channel."


def test_description_is_used_when_there_is_no_summary() -> None:
    schema = _input_schema(_action({"description": "Sends a message to a channel."}))
    assert schema["description"] == "Sends a message to a channel."


def test_summary_wins_over_description() -> None:
    op = {"summary": "Post a message", "description": "Long prose about posting."}
    assert _input_schema(_action(op))["description"] == "Post a message"


def test_only_the_first_paragraph_is_kept_with_links_flattened() -> None:
    op = {
        "description": (
            "Sends a message to a [channel](https://api.slack.com/channels).\n"
            "Wrapped onto a second line.\n\n"
            "## Arguments\n\nA whole reference section nobody needs in a tool listing."
        )
    }
    assert _operation_description(op) == (
        "Sends a message to a channel. Wrapped onto a second line."
    )


def test_description_is_capped_at_300_characters() -> None:
    text = _operation_description({"summary": "x" * 500})
    assert text is not None
    assert len(text) == 301  # 300 characters + the ellipsis
    assert text.endswith("…")


def test_no_operation_text_means_no_description() -> None:
    assert "description" not in _input_schema(_action({}))
    assert _operation_description({"summary": "   ", "description": ""}) is None


def test_parameter_descriptions_follow_the_short_description_rule() -> None:
    param = _param(
        "cursor",
        "Paginate through collections of data by setting the cursor parameter. See "
        "[pagination](#pagination) for more detail.",
    )
    schema = _input_schema(_action({}, [param]))
    assert schema["properties"]["cursor"]["description"] == (
        "Paginate through collections of data by setting the cursor parameter"
    )


def test_parameters_without_descriptions_get_none() -> None:
    schema = _input_schema(_action({}, [_param("cursor")]))
    assert "description" not in schema["properties"]["cursor"]


@pytest.mark.parametrize(
    "hostile",
    [
        'Says """hello""" and leaves',
        "Ends with a backslash \\",
        "Quote ' and \" mixed",
        '"""; import os; os.system("id"); """',
    ],
)
def test_spec_text_cannot_break_out_of_generated_source(hostile: str) -> None:
    """Descriptions are spec-controlled; they must round-trip as data, never as code."""
    schema = _input_schema(_action({"summary": hostile}, [_param("cursor", hostile)]))
    assert schema["description"] == hostile
    assert schema["properties"]["cursor"]["description"] == hostile


def test_derive_keeps_the_spec_parameter_description() -> None:
    doc = {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "parameters": [
                        {
                            "name": "cursor",
                            "in": "query",
                            "description": "Page cursor.",
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "description": "Max items."},
                        },
                    ],
                    "responses": {"204": {"description": "ok"}},
                }
            }
        },
    }
    result = derive_operations(doc, connector_id="demo")
    by_name = {p.wire_name: p for p in result.actions[0].params}
    assert by_name["cursor"].description == "Page cursor."
    # OAS 3 allows the description on the schema instead of the parameter.
    assert by_name["limit"].description == "Max items."
