# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated models publish JSON schemas without ``title``s.

Pydantic titles the model (its class name) and every field, where the title
restates the field name (``channel`` → ``"Channel"``). On a 174-tool connector
they were an eighth of ``tools/list``. Generated models drop them through a
``json_schema_extra`` hook emitted into ``schema.py``, so the schema is small at
the source and the bindings pass it through unchanged. Hand-written connectors
are not affected.
"""

from __future__ import annotations

import sys
import types
import uuid
from typing import Any

from nw_connector_builder.codegen import generate_schema_module
from nw_connector_builder.derive.operations import derive_operations
from node_wire_runtime.manifest import _schema_for

_DOC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "t", "version": "1"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/chat.postMessage": {
            "post": {
                "operationId": "chatPostMessage",
                "summary": "Sends a message to a channel.",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["channel"],
                                "properties": {
                                    "channel": {"type": "string", "description": "Where."},
                                    "title": {"type": "string"},
                                    "parse": {"type": "string", "enum": ["full", "none"]},
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"ok": {"type": "boolean"}},
                                }
                            }
                        },
                    }
                },
            }
        }
    },
}


def _module() -> types.ModuleType:
    src = generate_schema_module("demo", derive_operations(_DOC, connector_id="demo"))
    name = f"_nw_generated_{uuid.uuid4().hex}"
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(src, name, "exec"), module.__dict__)  # noqa: S102 — generated test fixture
    return module


def _titles(node: Any, path: str = "$") -> list[str]:
    """Paths of every `title` keyword in a schema (properties named `title` excluded)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "title" and isinstance(value, str):
                found.append(path)
            elif key == "properties" and isinstance(value, dict):
                for name, prop in value.items():
                    found += _titles(prop, f"{path}.{name}")
            else:
                found += _titles(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _titles(item, f"{path}[{i}]")
    return found


def test_input_schema_has_no_titles() -> None:
    assert _titles(_schema_for(_module().ChatPostMessageInput)) == []


def test_output_schema_has_no_titles() -> None:
    assert _titles(_schema_for(_module().ChatPostMessageOutput)) == []


def test_descriptions_and_contract_survive() -> None:
    schema = _schema_for(_module().ChatPostMessageInput)
    assert schema["description"] == "Sends a message to a channel."
    channel = schema["properties"]["channel"]
    assert channel["description"] == "Where"
    assert channel["type"] == "string"
    assert schema["required"] == ["channel"]


def test_a_field_named_title_is_kept() -> None:
    schema = _schema_for(_module().ChatPostMessageInput)
    assert "title" in schema["properties"]
