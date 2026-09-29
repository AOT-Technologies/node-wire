# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""stacklok's CLI and schema export, as the vendored ai-scoping skill calls them (Phase 1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from mcp_builder import cli
from nw_stacklok.schema import write_schema

from .conftest import FIXTURES


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> dict:
    monkeypatch.setattr(sys, "argv", ["mcp-builder", *args])
    cli.main()
    return json.loads(capsys.readouterr().out)


def test_analyze(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(monkeypatch, capsys, "analyze", str(FIXTURES / "petstore_openapi.json"))
    assert len(out["endpoints"]) == 19
    assert set(out["security_schemes"]) == {"api_key", "petstore_auth"}


def test_validate_accepts_a_node_wire_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scope = tmp_path / "scope.yaml"
    scope.write_text(
        (FIXTURES / "petstore.yaml").read_text()
        + "\nruntime:\n  type: node_wire\n  connector_id: pet_store\n",
        encoding="utf-8",
    )
    out = _run(
        monkeypatch,
        capsys,
        "validate",
        str(scope),
        "--openapi-spec",
        str(FIXTURES / "petstore_openapi.json"),
    )
    assert out == {"errors": [], "warnings": []}


def test_schema_export_includes_the_runtime_block(tmp_path: Path) -> None:
    schema = json.loads(write_schema(tmp_path / "mcp-scope-schema.json").read_text())
    assert "runtime" in schema["properties"]
    assert "NodeWireRuntime" in schema["$defs"]
