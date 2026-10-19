# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Draft-4 constructs that OpenAPI 3.0 forbids are repaired, not rejected.

Real Swagger 2.0 documents (Slack's published Web API spec among them) carry
union ``type`` lists, tuple-form ``items``, a bare ``type: "null"``, and
response-level ``examples``. Any one of them fails spec validation outright and
takes the whole build with it, even though every other part of the document is
usable.
"""

from __future__ import annotations

from typing import Any

from nw_connector_builder.normalize_v2 import (
    normalize_swagger2_to_openapi3,
    sanitize_draft4_schemas,
)


def test_type_union_with_null_becomes_nullable() -> None:
    doc = {"s": {"type": ["string", "null"], "pattern": "^x"}}
    counts = sanitize_draft4_schemas(doc)
    assert doc["s"] == {"type": "string", "nullable": True, "pattern": "^x"}
    assert counts["type_unions"] == 1


def test_type_union_of_several_concrete_types_becomes_any_of() -> None:
    doc: dict[str, Any] = {"s": {"type": ["string", "integer"]}}
    sanitize_draft4_schemas(doc)
    assert doc["s"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}


def test_bare_null_type_becomes_nullable() -> None:
    doc: dict[str, Any] = {"s": {"type": "null"}}
    counts = sanitize_draft4_schemas(doc)
    assert doc["s"] == {"nullable": True}
    assert counts["null_types"] == 1


def test_item_tuple_with_null_collapses_to_a_nullable_item() -> None:
    doc: dict[str, Any] = {"s": {"type": "array", "items": [{"type": "object"}, {"type": "null"}]}}
    counts = sanitize_draft4_schemas(doc)
    assert doc["s"]["items"] == {"type": "object", "nullable": True}
    assert counts["item_tuples"] == 1


def test_item_tuple_of_several_schemas_becomes_any_of() -> None:
    doc: dict[str, Any] = {"s": {"items": [{"type": "string"}, {"type": "integer"}]}}
    sanitize_draft4_schemas(doc)
    assert doc["s"]["items"] == {"anyOf": [{"type": "string"}, {"type": "integer"}]}


def test_valid_schemas_are_left_alone() -> None:
    doc: dict[str, Any] = {"s": {"type": "array", "items": {"type": "string"}, "nullable": False}}
    before = {"s": dict(doc["s"])}
    assert sanitize_draft4_schemas(doc) == {}
    assert doc == before


def test_swagger2_response_examples_move_into_content() -> None:
    """Swagger 2.0 keys response examples by mime on the response object itself."""
    converted = normalize_swagger2_to_openapi3(
        {
            "swagger": "2.0",
            "info": {"title": "t", "version": "1"},
            "host": "api.example.com",
            "paths": {
                "/x": {
                    "get": {
                        "operationId": "getX",
                        "produces": ["application/json"],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "schema": {"type": "object"},
                                "examples": {"application/json": {"ok": True}},
                            },
                            "default": {
                                "description": "err",
                                "examples": {"application/json": {"ok": False}},
                            },
                        },
                    }
                }
            },
        }
    )
    ok = converted["paths"]["/x"]["get"]["responses"]["200"]
    assert "examples" not in ok
    assert ok["content"]["application/json"]["example"] == {"ok": True}
    assert ok["content"]["application/json"]["schema"] == {"type": "object"}

    # A response with only examples still gets valid content, not a stray key.
    err = converted["paths"]["/x"]["get"]["responses"]["default"]
    assert "examples" not in err
    assert err["content"]["application/json"] == {"example": {"ok": False}}


def test_example_payloads_are_data_not_schemas() -> None:
    """GitHub's search example has ``items: [...]`` of strings; repairing it crashed the build."""
    payload = {"total_count": 2, "items": ["a", "b"], "type": ["x", "y"]}
    doc: dict[str, Any] = {
        "components": {
            "examples": {"search": {"value": payload}},
            "schemas": {
                "Thing": {
                    "type": "object",
                    "example": {"items": ["c"]},
                    "default": {"items": ["d"]},
                    "x-sample": {"items": ["e"]},
                    "properties": {
                        # Property names are not data keys, even when they look like one.
                        "example": {"type": ["string", "null"]},
                        "default": {"type": "array", "items": [{"type": "integer"}]},
                    },
                }
            },
        },
        "paths": {
            "/t": {
                "get": {
                    "responses": {
                        "default": {
                            "description": "d",
                            "content": {
                                "application/json": {"schema": {"type": ["integer", "null"]}}
                            },
                        }
                    }
                }
            }
        },
    }
    counts = sanitize_draft4_schemas(doc)
    assert doc["components"]["examples"]["search"]["value"] == payload
    thing = doc["components"]["schemas"]["Thing"]
    assert thing["example"] == {"items": ["c"]}
    assert thing["default"] == {"items": ["d"]}
    assert thing["x-sample"] == {"items": ["e"]}
    assert thing["properties"]["example"] == {"type": "string", "nullable": True}
    assert thing["properties"]["default"]["items"] == {"type": "integer"}
    schema = doc["paths"]["/t"]["get"]["responses"]["default"]["content"]["application/json"]
    assert schema["schema"] == {"type": "integer", "nullable": True}
    assert counts == {"type_unions": 2, "item_tuples": 1}
