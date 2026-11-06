# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Generated ``schema.py`` classes carry no redundant ``pass``.

Every generated Input model already declares ``model_config`` and ``action``,
and every object Output model declares ``model_config``, so a trailing ``pass``
is dead code. CodeQL flags each one (``py/unnecessary-pass``): the GitHub
connector alone raised 690 alerts.
"""

from __future__ import annotations

import ast
import sys
import types
import uuid
from typing import Any

from nw_connector_builder.codegen import generate_schema_module
from nw_connector_builder.derive.operations import derive_operations

_OBJECT_RESPONSE = {
    "200": {
        "description": "ok",
        "content": {
            "application/json": {
                "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}
            }
        },
    }
}

_DOC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "t", "version": "1"},
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        # No parameters and no body: the Input model has only model_config + action.
        "/meta": {"get": {"operationId": "metaRoot", "responses": _OBJECT_RESPONSE}},
        "/items": {
            "get": {
                "operationId": "listItems",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "responses": _OBJECT_RESPONSE,
            }
        },
    },
}


def _source() -> str:
    return generate_schema_module("demo", derive_operations(_DOC, connector_id="demo"))


def test_generated_classes_have_no_pass_statements() -> None:
    tree = ast.parse(_source())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert classes
    for cls in classes:
        assert not any(isinstance(stmt, ast.Pass) for stmt in cls.body), cls.name


def test_parameterless_models_still_build() -> None:
    name = f"_nw_generated_{uuid.uuid4().hex}"
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(_source(), name, "exec"), module.__dict__)  # noqa: S102 — generated test fixture
    assert module.MetaRootInput().action == "meta_root"
    assert module.MetaRootOutput(anything=1).model_extra == {"anything": 1}
