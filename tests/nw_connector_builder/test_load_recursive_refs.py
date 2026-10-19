# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Recursive schemas are cut at the point of recursion, not rejected.

Real-world specs model trees and back-references with ``$ref`` cycles — Microsoft
Graph's ``chatMessage.replies`` is a list of ``chatMessage``, and ``team`` and
``channel`` point at each other. Inlining every ``$ref`` never terminates on a
cycle, so the loader replaces the reference that would re-enter a schema with a
permissive object and builds everything else as usual.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from nw_connector_builder.load import load_openapi_document
from nw_connector_builder.pipeline import run_build

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = FIXTURES / "recursive.openapi.yaml"


def _body_schema(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["paths"]["/messages"]["post"]["requestBody"]["content"]["application/json"]["schema"]


def _team_schema(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["paths"]["/teams/{team_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]


def _is_cut(schema: dict[str, Any], name: str) -> bool:
    return (
        "$ref" not in schema
        and schema.get("type") == "object"
        and schema.get("x-recursive-ref", "").endswith(f"/components/schemas/{name}")
    )


def test_self_recursive_schema_loads_with_the_recursive_edge_cut() -> None:
    doc, _ = load_openapi_document(str(SPEC))
    message = _body_schema(doc)
    assert message["properties"]["content"] == {"type": "string"}
    assert _is_cut(message["properties"]["replies"]["items"], "Message")


def test_mutually_recursive_schemas_load_with_the_back_edge_cut() -> None:
    doc, _ = load_openapi_document(str(SPEC))
    channel = _team_schema(doc)["properties"]["channels"]["items"]
    assert channel["properties"]["id"] == {"type": "string"}
    assert _is_cut(channel["properties"]["team"], "Team")


def test_shared_non_recursive_schema_is_fully_inlined_everywhere() -> None:
    doc, _ = load_openapi_document(str(SPEC))
    for path, method, status in (("/messages", "post", "400"), ("/teams/{team_id}", "get", "404")):
        schema = doc["paths"][path][method]["responses"][status]["content"]["application/json"][
            "schema"
        ]
        assert schema == {"type": "object", "properties": {"code": {"type": "string"}}}


def test_cut_schemas_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="nw_connector_builder.load"):
        load_openapi_document(str(SPEC))
    text = caplog.text
    assert "recursive" in text
    assert "Message" in text and "Team" in text


def test_recursive_spec_builds_and_promotes(tmp_path: Path) -> None:
    root = tmp_path / "node-wire"
    (root / "src").mkdir(parents=True)
    (root / "packages" / "connectors").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")

    code = run_build(
        spec=str(SPEC),
        connector_id="recursive_demo",
        node_wire_root=root,
        no_mcp=True,
        report_path=tmp_path / "abort.json",
    )

    assert code == 0
    logic = (root / "src" / "node_wire_recursive_demo" / "logic.py").read_text()
    assert '@nw_action("create_message")' in logic
    assert '@nw_action("get_team")' in logic
