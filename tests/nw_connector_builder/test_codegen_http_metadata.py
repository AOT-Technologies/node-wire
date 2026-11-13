# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Codegen must publish method, path and OpenAPI tags on ``@nw_action`` so exposure
profiles can select on them."""

from __future__ import annotations

import ast

from nw_connector_builder.codegen import generate_logic_module
from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import ActionPlan, DeriveResult


def _decorator_kwargs(operation: dict) -> dict:
    action = ActionPlan(
        name="ping",
        method="GET",
        path="/ping/{id}",
        operation=operation,
        params=[],
        body_schema=None,
        body_media_type=None,
        output_schema=None,
        use_rest_response_output=True,
        auth=True,
    )
    result = DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://api.example.com",
        coverage_warning=False,
        total_operations=1,
    )
    tree = ast.parse(generate_logic_module("demo", result))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ping")
    call = fn.decorator_list[0]
    return {k.arg: ast.literal_eval(k.value) for k in call.keywords}


def test_decorator_publishes_method_path_and_tags() -> None:
    kwargs = _decorator_kwargs({"tags": ["pulls", "reviews"]})
    assert kwargs["http_method"] == "GET"
    assert kwargs["http_path"] == "/ping/{id}"
    assert kwargs["tags"] == ("pulls", "reviews")


def test_untagged_operation_omits_tags() -> None:
    assert "tags" not in _decorator_kwargs({})
