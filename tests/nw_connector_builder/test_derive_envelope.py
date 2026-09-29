# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Detection of the success-flag envelope (``{"ok": false}`` inside a 2xx body).

Only a *required boolean* named ``ok`` counts. Anything looser and a spec that
merely happens to have an ``ok`` field would start failing valid responses.
"""

from __future__ import annotations

import ast
from typing import Any

from nw_connector_builder.codegen import generate_logic_module
from nw_connector_builder.derive.operations import derive_operations


def _doc(schema: dict[str, Any] | None) -> dict[str, Any]:
    response: dict[str, Any] = {"description": "ok"}
    if schema is not None:
        response["content"] = {"application/json": {"schema": schema}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "t", "version": "1"},
        "servers": [{"url": "https://api.example.com"}],
        "paths": {
            "/ping": {
                "get": {"operationId": "ping", "responses": {"200": response}},
            }
        },
    }


def _flag(schema: dict[str, Any] | None) -> str | None:
    result = derive_operations(_doc(schema), connector_id="acme")
    return result.actions[0].envelope_ok_field


def test_required_boolean_ok_is_detected() -> None:
    assert (
        _flag(
            {
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}, "error": {"type": "string"}},
            }
        )
        == "ok"
    )


def test_optional_ok_is_ignored() -> None:
    """An optional flag says nothing when omitted — absence must not mean failure."""
    assert _flag({"type": "object", "properties": {"ok": {"type": "boolean"}}}) is None


def test_non_boolean_ok_is_ignored() -> None:
    assert (
        _flag({"type": "object", "required": ["ok"], "properties": {"ok": {"type": "string"}}})
        is None
    )


def test_unrelated_schema_is_ignored() -> None:
    assert _flag({"type": "object", "properties": {"id": {"type": "string"}}}) is None


def test_no_response_schema_is_ignored() -> None:
    assert _flag(None) is None


def test_detection_is_reported() -> None:
    result = derive_operations(
        _doc({"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}),
        connector_id="acme",
    )
    assert any("Success-flag envelope" in n and "ping" in n for n in result.notes)


def _execute_rest_kwargs(src: str, fn_name: str) -> dict[str, Any]:
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name
    )
    call = next(
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute_rest"
    )
    return {kw.arg: kw.value for kw in call.keywords}


def test_codegen_passes_the_flag_and_maps_the_error() -> None:
    result = derive_operations(
        _doc({"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}),
        connector_id="acme",
    )
    src = generate_logic_module("acme", result)
    kwargs = _execute_rest_kwargs(src, "ping")
    assert ast.literal_eval(kwargs["envelope_ok_field"]) == "ok"
    assert "RestEnvelopeError" in src
    assert 'RestEnvelopeError: (ErrorCategory.BUSINESS, "API_ENVELOPE_ERROR")' in src


def test_codegen_omits_everything_when_no_envelope() -> None:
    """Specs without the convention must generate exactly what they did before."""
    result = derive_operations(_doc({"type": "object", "properties": {}}), connector_id="acme")
    src = generate_logic_module("acme", result)
    assert "envelope_ok_field" not in src
    assert "RestEnvelopeError" not in src
