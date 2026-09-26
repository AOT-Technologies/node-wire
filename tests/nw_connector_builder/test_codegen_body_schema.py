# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated input models expose object-body properties as top-level fields.

The Pydantic model is the single contract every binding enforces — REST, gRPC
and MCP all ``model_validate`` against it — so required body fields, types and
enums hold everywhere, not only where an MCP client validates the advertised
schema.
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from nw_connector_builder.codegen import (
    _short_description,
    generate_model_tests,
    generate_schema_module,
)
from nw_connector_builder.derive.operations import derive_operations
from node_wire_runtime.manifest import _schema_for, _strip_action_field_from_json_schema


def _doc(op: dict[str, Any]) -> dict[str, Any]:
    op = {"operationId": "chatPostMessage", "responses": {"200": {"description": "ok"}}, **op}
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {"/chat.postMessage": {"post": op}},
    }


def _json_body(schema: dict[str, Any], *, required: bool = True) -> dict[str, Any]:
    return {"required": required, "content": {"application/json": {"schema": schema}}}


_CHAT = _doc(
    {
        "requestBody": _json_body(
            {
                "type": "object",
                "required": ["channel", "text"],
                "properties": {
                    "channel": {"type": "string", "description": "Channel to send to."},
                    "text": {"type": "string"},
                    "parse": {"type": "string", "enum": ["full", "none"]},
                    "from": {"type": "string"},
                },
            }
        )
    }
)


def _load(doc: dict[str, Any]) -> types.ModuleType:
    """Derive + generate + import a schema module under a throwaway name."""
    result = derive_operations(doc, connector_id="demo")
    src = generate_schema_module("demo", result)
    name = f"_nw_generated_{uuid.uuid4().hex}"
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(src, name, "exec"), module.__dict__)  # noqa: S102 — generated test fixture
    return module


def _advertised(model: Any) -> dict[str, Any]:
    schema = _schema_for(model)
    _strip_action_field_from_json_schema(schema)
    return schema


def test_body_properties_are_top_level_and_required_fields_enforced() -> None:
    model = _load(_CHAT).ChatPostMessageInput
    schema = _advertised(model)
    assert set(schema["properties"]) == {"channel", "text", "parse", "from_"}
    assert "body" not in schema["properties"]
    assert schema["required"] == ["channel", "text"]
    assert schema["properties"]["channel"]["description"] == "Channel to send to"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": "hi"},
        {"body": {"channel": "C1", "text": "hi"}},  # the old nested shape is gone
        {"channel": "C1", "text": "hi", "parse": "markdown"},  # not in the enum
    ],
)
def test_model_rejects_what_the_contract_forbids(payload: dict[str, Any]) -> None:
    """The REST/gRPC path: only Pydantic stands between the caller and the API."""
    with pytest.raises(ValidationError):
        _load(_CHAT).ChatPostMessageInput.model_validate(payload)


def test_model_accepts_the_flat_shape_and_the_wire_name_alias() -> None:
    model = _load(_CHAT).ChatPostMessageInput
    by_field = model.model_validate({"channel": "C1", "text": "hi", "from_": "bot"})
    by_wire = model.model_validate({"channel": "C1", "text": "hi", "from": "bot"})
    assert by_field.from_ == by_wire.from_ == "bot"


def test_string_enums_are_advertised() -> None:
    """As an MCP client sees it — after the binding drops the optional-field null union."""
    from bindings.mcp_server.server import mcp_llm_safe_input_schema

    schema = mcp_llm_safe_input_schema(_advertised(_load(_CHAT).ChatPostMessageInput))
    assert schema["properties"]["parse"]["enum"] == ["full", "none"]


def test_colliding_parameter_gets_no_alias() -> None:
    """`channel` must populate the body field only, not also the renamed query param."""
    doc = _doc(
        {
            "parameters": [{"name": "channel", "in": "query", "schema": {"type": "string"}}],
            "requestBody": _json_body(
                {"type": "object", "properties": {"channel": {"type": "string"}}}
            ),
        }
    )
    model = _load(doc).ChatPostMessageInput
    parsed = model.model_validate({"channel": "B", "channel__query": "Q"})
    assert parsed.channel == "B"
    assert parsed.channel__query == "Q"
    assert model.model_validate({"channel": "B"}).channel__query is None


def test_array_body_stays_a_single_required_list_argument() -> None:
    doc = _doc({"requestBody": _json_body({"type": "array", "items": {"type": "string"}})})
    model = _load(doc).ChatPostMessageInput
    assert model.model_validate({"body": ["a", "b"]}).body == ["a", "b"]
    with pytest.raises(ValidationError):
        model.model_validate({})


def test_optional_free_form_body_stays_optional() -> None:
    doc = _doc({"requestBody": _json_body({"type": "object"}, required=False)})
    model = _load(doc).ChatPostMessageInput
    assert model.model_validate({}).body is None


def test_generated_example_test_runs_with_json_literals() -> None:
    """Examples holding true/false/null must become valid Python, mapped onto flat fields."""
    doc = _doc(
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["channel"],
                            "properties": {
                                "channel": {"type": "string"},
                                "unfurl": {"type": "boolean"},
                                "thread": {"type": "string", "nullable": True},
                            },
                        },
                        "example": {
                            "channel": "C1",
                            "unfurl": True,
                            "thread": None,
                            "undocumented": 1,
                        },
                    }
                },
            }
        }
    )
    result = derive_operations(doc, connector_id="demo")
    schema_mod = types.ModuleType("node_wire_demo.schema")
    sys.modules["node_wire_demo.schema"] = schema_mod
    package = types.ModuleType("node_wire_demo")
    package.schema = schema_mod  # type: ignore[attr-defined]
    sys.modules["node_wire_demo"] = package
    try:
        exec(compile(generate_schema_module("demo", result), "schema", "exec"), schema_mod.__dict__)  # noqa: S102
        tests_src = generate_model_tests("demo", result)
        namespace: dict[str, Any] = {}
        exec(compile(tests_src, "tests", "exec"), namespace)  # noqa: S102
        namespace["test_chat_post_message_example_parses"]()
    finally:
        del sys.modules["node_wire_demo.schema"]
        del sys.modules["node_wire_demo"]


def test_descriptions_are_trimmed_to_one_sentence_without_markdown_links() -> None:
    desc = (
        "Channel, private group, or IM channel to send message to. Can be an encoded ID, "
        "or a name. See [below](#channels) for more details."
    )
    assert _short_description(desc) == "Channel, private group, or IM channel to send message to"
