# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for review fixes (#1 output envelope, field dedupe, etc.)."""

from __future__ import annotations

from nw_connector_builder.codegen import generate_model_tests, generate_schema_module
from nw_connector_builder.derive.operations import (
    ActionPlan,
    ParamPlan,
    _dedupe_field_names,
    _schema_is_object,
    _success_response_schema,
)
from node_wire_runtime.rest import _looks_base64


def test_array_success_schema_uses_envelope() -> None:
    op = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {"schema": {"type": "array", "items": {"type": "string"}}}
                }
            }
        }
    }
    schema, use_envelope = _success_response_schema(op)
    assert schema is not None
    assert use_envelope is True


def test_object_success_schema_uses_typed_path() -> None:
    op = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                        }
                    }
                }
            }
        }
    }
    schema, use_envelope = _success_response_schema(op)
    assert schema is not None
    assert use_envelope is False
    assert _schema_is_object(schema)


def test_unresolved_ref_success_uses_envelope() -> None:
    op = {
        "responses": {
            "200": {
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Pet"}}}
            }
        }
    }
    _, use_envelope = _success_response_schema(op)
    assert use_envelope is True


def test_dedupe_field_names() -> None:
    params = [
        ParamPlan(
            field_name="user_id",
            wire_name="user-id",
            location="query",
            required=False,
            schema={"type": "string"},
        ),
        ParamPlan(
            field_name="user_id",
            wire_name="user.id",
            location="header",
            required=False,
            schema={"type": "string"},
        ),
    ]
    out = _dedupe_field_names(params)
    assert [p.field_name for p in out] == ["user_id", "user_id_2"]


def test_example_test_includes_required_params() -> None:
    from nw_connector_builder.derive.auth import ConnectorAuthPlan
    from nw_connector_builder.derive.operations import DeriveResult

    action = ActionPlan(
        name="update_pet",
        method="POST",
        path="/pets/{petId}",
        operation={},
        params=[
            ParamPlan(
                field_name="pet_id",
                wire_name="petId",
                location="path",
                required=True,
                schema={"type": "string"},
                python_type_hint="str",
            )
        ],
        body_schema={"type": "object"},
        body_media_type="application/json",
        output_schema=None,
        use_rest_response_output=True,
        auth=True,
        examples={"request": {"name": "Spot"}},
    )
    result = DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://api.example.com",
        coverage_warning=False,
        total_operations=1,
    )
    src = generate_model_tests("demo", result)
    assert "test_update_pet_example_parses" in src
    assert "'pet_id':" in src
    assert "'body': example" in src


def test_example_test_skipped_without_body_field() -> None:
    from nw_connector_builder.derive.auth import ConnectorAuthPlan
    from nw_connector_builder.derive.operations import DeriveResult

    action = ActionPlan(
        name="ping",
        method="GET",
        path="/ping",
        operation={},
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema=None,
        use_rest_response_output=True,
        auth=False,
        examples={"request": {"ignored": True}},
    )
    result = DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://api.example.com",
        coverage_warning=False,
        total_operations=1,
    )
    src = generate_model_tests("demo", result)
    assert "example_parses" not in src


def test_typed_output_emitted_only_when_not_envelope() -> None:
    from nw_connector_builder.derive.auth import ConnectorAuthPlan
    from nw_connector_builder.derive.operations import DeriveResult

    typed = ActionPlan(
        name="get_pet",
        method="GET",
        path="/pets/{id}",
        operation={},
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema={"type": "object"},
        use_rest_response_output=False,
        auth=True,
    )
    arrayish = ActionPlan(
        name="list_pets",
        method="GET",
        path="/pets",
        operation={},
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema={"type": "array"},
        use_rest_response_output=True,
        auth=True,
    )
    result = DeriveResult(
        actions=[typed, arrayish],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://api.example.com",
        coverage_warning=False,
        total_operations=2,
    )
    src = generate_schema_module("demo", result)
    assert "class GetPetOutput(BaseModel):" in src
    assert "ListPetsOutput = RestResponseOutput" in src


def test_looks_base64_rejects_short_form_strings() -> None:
    assert _looks_base64("abcdefgh") is False
    # 64 chars of alphabet without padding
    assert _looks_base64("a" * 64) is False
    # Valid padded base64 (≥64 chars, 0–2 '=' padding)
    padded = ("ABCD" * 15) + "ab=="  # 60 + 4 = 64
    assert _looks_base64(padded) is True
