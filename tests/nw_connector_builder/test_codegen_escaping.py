# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for C-2 (2026-09-01 security review): generated Python/TOML
source must never let a spec-controlled value (connector_id, default_base_url)
break out of the string literal it's interpolated into.
"""

from __future__ import annotations

import ast

import pytest

from nw_connector_builder.codegen import (
    generate_init_module,
    generate_logic_module,
    generate_package_pyproject,
    generate_schema_module,
)
from nw_connector_builder.derive.auth import ConnectorAuthPlan
from nw_connector_builder.derive.operations import ActionPlan, DeriveResult

_MALICIOUS_BASE_URL = 'https://x.example"; import os; os.system("id"); x="'


def _result(default_base_url: str) -> DeriveResult:
    # An empty `actions` list would itself generate an invalid `from .schema
    # import ()` — a pre-existing, unrelated codegen edge case never hit by
    # real specs (which always have >=1 operation). Use one real action so
    # this test only exercises the C-2 escaping fix.
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
    )
    return DeriveResult(
        actions=[action],
        drops=[],
        auth_plan=ConnectorAuthPlan(None, None, "none", "", {}, []),
        default_base_url=default_base_url,
        coverage_warning=False,
        total_operations=1,
    )


def test_malicious_base_url_cannot_break_out_of_string_literal() -> None:
    src = generate_logic_module("demo", _result(_MALICIOUS_BASE_URL))
    # Must parse as a single valid module — a successful escape would either
    # fail to parse or parse into extra top-level statements (the injected
    # `import os` / `os.system(...)` calls landing outside the string).
    tree = ast.parse(src)
    compile(src, "<generated>", "exec")  # syntax-valid; doesn't require the sibling .schema module
    import_modules = {
        node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
    }
    # `httpx` is the one legitimate `import` statement this module always
    # emits; anything else (e.g. the payload's `import os`) means the string
    # literal was broken out of.
    assert import_modules == {"httpx"}, f"unexpected top-level import(s): {import_modules}"

    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    assigned = {
        stmt.targets[0].id: stmt.value.value
        for stmt in class_def.body
        if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name)
    }
    # The malicious payload must round-trip byte-for-byte as the *value* of
    # the assignment, not as executable syntax spliced around it.
    assert assigned["default_base_url"] == _MALICIOUS_BASE_URL


@pytest.mark.parametrize(
    "bad_id",
    [
        "../evil",
        "demo; import os",
        'demo"',
        "demo\nimport os",
        "Demo",
        "1demo",
        "",
    ],
)
def test_invalid_connector_id_rejected_everywhere(bad_id: str) -> None:
    result = _result("https://api.example.com")
    for fn, args in [
        (generate_schema_module, (bad_id, result)),
        (generate_logic_module, (bad_id, result)),
        (generate_init_module, (bad_id,)),
        (generate_package_pyproject, (bad_id,)),
    ]:
        with pytest.raises(ValueError, match="invalid connector_id"):
            fn(*args)  # type: ignore[operator]


def test_valid_connector_id_still_generates_clean_source() -> None:
    result = _result("https://api.example.com")
    for src in (
        generate_schema_module("demo_pets", result),
        generate_logic_module("demo_pets", result),
        generate_init_module("demo_pets"),
        generate_package_pyproject("demo_pets"),
    ):
        assert isinstance(src, str) and src
    ast.parse(generate_logic_module("demo_pets", result))
    ast.parse(generate_schema_module("demo_pets", result))
    ast.parse(generate_init_module("demo_pets"))
