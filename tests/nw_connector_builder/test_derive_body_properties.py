# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Object request bodies become top-level call arguments.

The tool contract is flat — one named, typed, described argument per body
property — and each argument's ``location="body_property"`` tells the runtime
to reassemble the request body on the wire. Bodies that are not objects with
declared properties keep a single ``body`` argument.
"""

from __future__ import annotations

from typing import Any

import pytest

from nw_connector_builder.derive.operations import ActionPlan, derive_operations
from nw_connector_builder.normalize_v2 import normalize_swagger2_to_openapi3


def _doc(
    request_body: dict[str, Any] | None,
    *,
    params: list[dict[str, Any]] | None = None,
    anonymous: bool = False,
) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationId": "postMessage",
        "parameters": params or [],
        "responses": {"200": {"description": "ok"}},
    }
    if request_body is not None:
        op["requestBody"] = request_body
    if anonymous:
        op["security"] = []
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "security": [{"apiKeyAuth": []}],
        "paths": {"/chat.postMessage": {"post": op}},
        "components": {
            "securitySchemes": {
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
            }
        },
    }


def _body(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    body_required: bool = True,
    media: str = "application/json",
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"required": body_required, "content": {media: {"schema": schema}}}


def _action(doc: dict[str, Any]) -> ActionPlan:
    return derive_operations(doc, connector_id="acme").actions[0]


def _by_field(action: ActionPlan) -> dict[str, Any]:
    return {p.field_name: p for p in action.params}


def test_object_body_properties_become_arguments() -> None:
    action = _action(
        _doc(
            _body(
                {
                    "channel": {"type": "string", "description": "Channel to send to."},
                    "text": {"type": "string"},
                },
                required=["channel"],
            )
        )
    )
    fields = _by_field(action)
    assert set(fields) == {"channel", "text"}
    assert fields["channel"].location == "body_property"
    assert fields["channel"].wire_name == "channel"
    assert fields["channel"].media_type == "application/json"
    assert fields["channel"].python_type_hint == "str"
    assert fields["channel"].description == "Channel to send to."
    assert action.body_flattened is True


def test_property_is_required_only_when_the_body_is_required() -> None:
    required = _by_field(
        _action(_doc(_body({"channel": {"type": "string"}}, required=["channel"])))
    )
    optional = _by_field(
        _action(
            _doc(_body({"channel": {"type": "string"}}, required=["channel"], body_required=False))
        )
    )
    assert required["channel"].required is True
    assert optional["channel"].required is False


def test_form_encoded_body_is_flattened_with_its_media_type() -> None:
    action = _action(
        _doc(_body({"channel": {"type": "string"}}, media="application/x-www-form-urlencoded"))
    )
    assert _by_field(action)["channel"].media_type == "application/x-www-form-urlencoded"


def test_credential_inside_the_body_is_dropped_for_authenticated_actions() -> None:
    """Slack's spec puts `token` in the form body of 11 operations, as a required field."""
    doc = _doc(
        _body(
            {"token": {"type": "string"}, "name": {"type": "string"}},
            required=["token", "name"],
            media="application/x-www-form-urlencoded",
        )
    )
    result = derive_operations(doc, connector_id="acme")
    assert set(_by_field(result.actions[0])) == {"name"}
    assert any("token (in body)" in note for note in result.notes)


def test_credential_inside_the_body_is_kept_for_anonymous_actions() -> None:
    doc = _doc(_body({"token": {"type": "string"}}, required=["token"]), anonymous=True)
    assert "token" in _by_field(_action(doc))


def test_read_only_properties_are_not_request_arguments() -> None:
    action = _action(
        _doc(_body({"id": {"type": "string", "readOnly": True}, "name": {"type": "string"}}))
    )
    assert set(_by_field(action)) == {"name"}


def test_parameter_that_collides_with_a_body_property_is_renamed_by_location() -> None:
    action = _action(
        _doc(
            _body({"channel": {"type": "string"}}),
            params=[{"name": "channel", "in": "query", "schema": {"type": "string"}}],
        )
    )
    fields = _by_field(action)
    assert fields["channel"].location == "body_property"
    assert fields["channel__query"].location == "query"
    assert fields["channel__query"].wire_name == "channel"


@pytest.mark.parametrize("name", ["action", "body", "config_name", "tenant_id", "trace_id"])
def test_reserved_argument_names_are_renamed_but_keep_their_wire_name(name: str) -> None:
    """The bindings consume these names themselves (MCP pops tenant_id/config_name)."""
    fields = _by_field(_action(_doc(_body({name: {"type": "string"}}))))
    assert name not in fields
    (plan,) = fields.values()
    assert plan.wire_name == name


def test_python_keywords_become_valid_field_names() -> None:
    fields = _by_field(_action(_doc(_body({"from": {"type": "string"}}))))
    assert fields["from_"].wire_name == "from"


def test_all_of_parts_are_merged() -> None:
    body = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "allOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {
                            "type": "object",
                            "required": ["b"],
                            "properties": {"b": {"type": "integer"}},
                        },
                    ]
                }
            }
        },
    }
    fields = _by_field(_action(_doc(body)))
    assert set(fields) == {"a", "b"}
    assert fields["b"].required is True


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "object"},
        {"type": "object", "additionalProperties": {"type": "string"}},
        {"oneOf": [{"type": "object", "properties": {"a": {"type": "string"}}}]},
        {"type": "string", "format": "binary"},
    ],
)
def test_bodies_without_declared_object_properties_stay_whole(schema: dict[str, Any]) -> None:
    body = {"required": True, "content": {"application/json": {"schema": schema}}}
    action = _action(_doc(body))
    assert action.body_flattened is False
    assert action.body_required is True
    assert all(p.location != "body_property" for p in action.params)


def test_non_form_non_json_media_stays_whole() -> None:
    action = _action(_doc(_body({"a": {"type": "string"}}, media="application/xml")))
    assert action.body_flattened is False


def test_swagger2_body_parameter_keeps_its_required_flag() -> None:
    """OAS 2 `in: body` defaults to optional; the converter used to force required."""
    swagger = {
        "swagger": "2.0",
        "info": {"title": "t", "version": "1"},
        "host": "api.example.com",
        "paths": {
            "/items": {
                "post": {
                    "parameters": [{"name": "payload", "in": "body", "schema": {"type": "object"}}],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    oas3 = normalize_swagger2_to_openapi3(swagger)
    assert oas3["paths"]["/items"]["post"]["requestBody"]["required"] is False
