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
    (logic_dir / "logic.py").write_text(
        '''\
from node_wire_runtime import nw_action

@nw_action("ping")
def ping():
    return {"ok": True}

@nw_action("files.list")
def files_list():
    return []
''',
        encoding="utf-8",
    )

    bindings = root / "src" / "bindings"
    bindings.mkdir(parents=True)
    (bindings / "__init__.py").write_text("", encoding="utf-8")
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
