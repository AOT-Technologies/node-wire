# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for MCP hand-off after promote."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from nw_connector_builder.mcp_handoff import run_mcp_handoff


def test_mcp_handoff_success(tmp_path: Path) -> None:
    project = tmp_path / "out" / "pet-store-nw-mcp"
    with patch(
        "nw_mcp_builder.from_connector.run_from_connector",
        return_value=project,
    ) as run:
        result = run_mcp_handoff("pet_store", node_wire_root=tmp_path, force_output=True)
    assert result == {"ok": True, "project": str(project)}
    run.assert_called_once()
    kwargs = run.call_args.kwargs
    assert kwargs["force_fixture"] is True
    assert kwargs["force_output"] is True
    assert kwargs["package_root"] == tmp_path / "nw-mcp-builder"


def test_mcp_handoff_runtime_error(tmp_path: Path) -> None:
    with patch(
        "nw_mcp_builder.from_connector.run_from_connector",
        side_effect=RuntimeError("wheels missing"),
    ):
        result = run_mcp_handoff("pet_store", node_wire_root=tmp_path, force_output=False)
    assert result["ok"] is False
    assert "wheels missing" in result["error"]


def test_mcp_handoff_import_error(tmp_path: Path) -> None:
    with patch.dict(sys.modules, {"nw_mcp_builder.from_connector": None}):
        result = run_mcp_handoff("pet_store", node_wire_root=tmp_path, force_output=False)
    assert result["ok"] is False
    assert "not importable" in result["error"]
