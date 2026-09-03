#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Path setup and shared fixtures for nw-mcp-builder tests."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

_NW_MCP_BUILDER_SRC = Path(__file__).resolve().parents[2] / "nw-mcp-builder" / "src"
if str(_NW_MCP_BUILDER_SRC) not in sys.path:
    sys.path.insert(0, str(_NW_MCP_BUILDER_SRC))


def _touch_wheel(path: Path, *, package_dir: str) -> None:
    """Write a minimal valid .whl (zip) with no .py payload required for copy tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{package_dir}/RECORD", "")


@pytest.fixture
def fake_node_wire(tmp_path: Path) -> Path:
    """Minimal node-wire layout for connector ``demo_conn``."""
    root = tmp_path / "node-wire"
    root.mkdir(parents=True)
    connector_id = "demo_conn"
    (root / "pyproject.toml").write_text('[project]\nname = "node-wire"\n', encoding="utf-8")

    pkg = root / "packages" / "connectors" / connector_id
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        f'[project]\nname = "node-wire-{connector_id.replace("_", "-")}"\n',
        encoding="utf-8",
    )

    logic_dir = root / "src" / f"node_wire_{connector_id}"
    logic_dir.mkdir(parents=True)
    (logic_dir / "__init__.py").write_text("", encoding="utf-8")
    # A real BaseConnector subclass, not a text fixture for a regex scanner: discover_actions
    # imports this module for real and reads its registered action metadata (see
    # docs/adr/, candidate 7 of the architecture review — live import replaced regex parsing).
    # Uses the real, already-installed node_wire_runtime (this src/ tree is appended to
    # sys.path, not inserted at the front, so nothing here can shadow it).
    (logic_dir / "logic.py").write_text(
        """\
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from node_wire_runtime import BaseConnector, nw_action


class PingInput(BaseModel):
    action: Literal["ping"] = "ping"


class PingOutput(BaseModel):
    ok: bool = True


class FilesListInput(BaseModel):
    action: Literal["files.list"] = "files.list"


class FilesListOutput(BaseModel):
    files: list = []


class DemoConnConnector(BaseConnector):
    connector_id = "demo_conn"
    output_model = PingOutput

    @nw_action("ping")
    async def ping(self, params: PingInput, *, trace_id: str) -> PingOutput:
        return PingOutput()

    @nw_action("files.list")
    async def files_list(self, params: FilesListInput, *, trace_id: str) -> FilesListOutput:
        return FilesListOutput()
""",
        encoding="utf-8",
    )

    bindings = root / "src" / "bindings"
    bindings.mkdir(parents=True)
    (bindings / "__init__.py").write_text("", encoding="utf-8")
    # Needed for _vendor_minimal_node_wire_src (generate/connector_project.py), which
    # copies this directory into the generated MCP host's vendor/ tree — unrelated to
    # discover_actions, which inserts this src/ tree at the front of sys.path (same as
    # gate.py); node_wire_runtime itself is unaffected since it's already in sys.modules
    # by the time any test runs, and the module cache wins before sys.path is consulted.
    runtime = root / "src" / "node_wire_runtime"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("", encoding="utf-8")

    config_dir = root / "config"
    config_dir.mkdir()
    (config_dir / "connectors.yaml").write_text(
        f"""\
connectors:
  {connector_id}:
    enabled: true
    auth:
      type: service_account
      sa_json_secret: DEMO_CONN_SA_JSON
""",
        encoding="utf-8",
    )

    (root / "sample.env").write_text(
        "DEMO_CONN_TOKEN=secret\nOTHER_VAR=x\n",
        encoding="utf-8",
    )

    _touch_wheel(
        root / "packages" / "runtime" / "dist" / "node_wire_runtime-1.0.0-py3-none-any.whl",
        package_dir="node_wire_runtime-1.0.0.dist-info",
    )
    _touch_wheel(
        pkg / "dist" / "node_wire_demo_conn-1.0.0-py3-none-any.whl",
        package_dir="node_wire_demo_conn-1.0.0.dist-info",
    )
    return root


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    """nw-mcp-builder package root with out/ and fixtures/."""
    root = tmp_path / "nw-mcp-builder"
    (root / "out").mkdir(parents=True)
    (root / "fixtures").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "nw-mcp-builder"\n', encoding="utf-8")
    return root
