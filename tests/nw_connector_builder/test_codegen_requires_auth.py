# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Codegen must publish ``requires_auth`` on ``@nw_action``, not only pass
``auth=False`` to ``execute_rest``, or anonymous actions lie in the manifest.
"""

from __future__ import annotations

import ast

from nw_connector_builder.codegen import generate_logic_module
from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import ActionPlan, DeriveResult


def _result(auth: bool, auth_scheme_name: str | None = None) -> DeriveResult:
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
        auth=auth,
        auth_scheme_name=auth_scheme_name,
    )
    return DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url="https://api.example.com",
        coverage_warning=False,
        total_operations=1,
    )


def _decorator_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ping")
    return fn.decorator_list[0]


def _execute_rest_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "ping")
    ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
    value = ret.value.value if isinstance(ret.value, ast.Await) else ret.value  # unwrap `await`
    assert isinstance(value, ast.Call)
    return value


def test_anonymous_action_decorator_publishes_requires_auth_false() -> None:
    src = generate_logic_module("demo", _result(auth=False))
    call = _decorator_call(src)
    kwargs = {
        kw.arg: (kw.value.value if isinstance(kw.value, ast.Constant) else kw.value)
        for kw in call.keywords
    }
    assert kwargs == {"requires_auth": False}
    # Still skips auth on the wire — this test only covers the decorator metadata gap.
    assert "auth=False" in src


def test_authed_action_decorator_has_no_requires_auth_kwarg() -> None:
    """Unchanged behavior for authed actions — sdk_action() already defaults
    requires_auth=True, so emitting it explicitly here would just be noise."""
    src = generate_logic_module("demo", _result(auth=True))
    call = _decorator_call(src)
    assert call.keywords == []


def test_action_with_named_auth_scheme_emits_auth_scheme_kwarg() -> None:
    """Non-default scheme must pass auth_scheme=<name> to execute_rest()."""
    src = generate_logic_module("demo", _result(auth=True, auth_scheme_name="petstore_auth"))
    call = _execute_rest_call(src)
    kwargs = {
        kw.arg: (kw.value.value if isinstance(kw.value, ast.Constant) else kw.value)
        for kw in call.keywords
    }
    assert kwargs["auth_scheme"] == "petstore_auth"
    assert kwargs.get("auth", True) is True  # no `auth=False` — this action still needs auth


def test_action_without_named_auth_scheme_has_no_auth_scheme_kwarg() -> None:
    """The overwhelming majority of actions — unchanged, no new kwarg noise."""
    src = generate_logic_module("demo", _result(auth=True, auth_scheme_name=None))
    call = _execute_rest_call(src)
    kwargs = {kw.arg for kw in call.keywords}
    assert "auth_scheme" not in kwargs


def test_auth_scheme_name_is_safely_escaped_against_codegen_injection() -> None:
    """auth_scheme_name comes from an OpenAPI securitySchemes key in a possibly-remote,
    untrusted spec — same C-2 escaping class as connector_id/default_base_url
    (test_codegen_escaping.py). It must round-trip as a string value, never break out
    of the generated source.
    """
    malicious = 'x"; import os; os.system("id"); y="'
    src = generate_logic_module("demo", _result(auth=True, auth_scheme_name=malicious))
    tree = ast.parse(src)  # must still parse as a single valid module
    call = _execute_rest_call(src)
    kwargs = {
        kw.arg: (kw.value.value if isinstance(kw.value, ast.Constant) else kw.value)
        for kw in call.keywords
    }
    assert kwargs["auth_scheme"] == malicious
    import_modules = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    assert import_modules == {"httpx"}
