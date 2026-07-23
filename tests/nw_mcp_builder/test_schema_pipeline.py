#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Scope schema and pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from nw_mcp_builder.pipeline import run_connector_pipeline
from nw_mcp_builder.schema.models import load_scope


def _minimal_scope(*, connector_id: str = "demo_conn", runtime: bool = True) -> dict:
    doc: dict = {
        "version": "1",
        "server": {"name": "demo-conn-nw", "description": "demo"},
        "spec": {
            "source": "node-wire/src/node_wire_demo_conn",
            "format": "openapi3",
            "base_url": "https://unused.invalid",
        },
        "groups": [
            {
                "name": "connector",
                "description": "tools",
                "tools": [
                    {
                        "tool_name": "ping",
                        "endpoint": "POST /connectors/demo_conn/ping",
                        "description": "ping",
                        "response_kind": "json",
                    }
                ],
            }
        ],
        "auth": {"type": "none"},
    }
    if runtime:
        doc["runtime"] = {"type": "node_wire", "connector_id": connector_id}
    return doc


def test_load_scope_valid(tmp_path: Path) -> None:
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(_minimal_scope()), encoding="utf-8")
    scope = load_scope(path)
    assert scope.runtime is not None
    assert scope.runtime.connector_id == "demo_conn"
    assert scope.server.name == "demo-conn-nw"


def test_load_scope_rejects_bad_server_name(tmp_path: Path) -> None:
    doc = _minimal_scope()
    doc["server"]["name"] = "Bad_Name"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scope(path)


def test_load_existing_google_drive_fixture() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "nw-mcp-builder"
        / "fixtures"
        / "google_drive_nw.yaml"
    )
    if not fixture.is_file():
        pytest.skip("google_drive fixture not present")
    scope = load_scope(fixture)
    assert scope.runtime is not None
    assert scope.runtime.connector_id == "google_drive"


def test_pipeline_rejects_non_connector_scope(tmp_path: Path, fake_node_wire: Path) -> None:
    path = tmp_path / "openapi.yaml"
    path.write_text(yaml.safe_dump(_minimal_scope(runtime=False)), encoding="utf-8")
    with pytest.raises(ValueError, match="not connector mode"):
        run_connector_pipeline(path, fake_node_wire, tmp_path / "out")


def test_pipeline_writes_project(tmp_path: Path, fake_node_wire: Path) -> None:
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(_minimal_scope()), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    project = run_connector_pipeline(path, fake_node_wire, out)
    assert project.is_dir()
    assert (project / "src" / "demo_conn_nw_mcp" / "__main__.py").is_file()
