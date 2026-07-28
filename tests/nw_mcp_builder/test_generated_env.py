#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Generated host ``_load_env`` behaviour (ToolHive process env vs volume .env)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from nw_mcp_builder.generate.connector_project import _main_py


def _load_generated_module(project_dir: Path, connector_id: str = "demo_conn"):
    module_dir = project_dir / "src" / f"{connector_id}_nw_mcp"
    module_dir.mkdir(parents=True, exist_ok=True)
    main_path = module_dir / "__main__.py"
    main_path.write_text(_main_py(connector_id=connector_id), encoding="utf-8")
    # Project root markers used by _project_root()
    (project_dir / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
    (project_dir / "config").mkdir(exist_ok=True)
    (project_dir / "vendor" / "node_wire_src" / "bindings").mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location(
        f"nw_mcp_generated_{connector_id}",
        main_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_env_ok_without_dotenv_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_LOAD_DOTENV", raising=False)
    monkeypatch.setenv("GOOGLE_DRIVE_SA_JSON", "from-toolhive")
    mod = _load_generated_module(tmp_path / "host", "google_drive")
    mod._load_env()
    assert os.environ["NW_REST_LOAD_DOTENV"] == "false"
    assert os.environ["GOOGLE_DRIVE_SA_JSON"] == "from-toolhive"


def test_load_env_file_fills_unset_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "host"
    mod = _load_generated_module(project, "google_drive")
    (project / ".env").write_text(
        "GOOGLE_DRIVE_SA_JSON=from-file\nGOOGLE_DRIVE_FOLDER_ID=folder-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_DRIVE_SA_JSON", "from-toolhive")
    monkeypatch.delenv("GOOGLE_DRIVE_FOLDER_ID", raising=False)

    mod._load_env()
    assert os.environ["GOOGLE_DRIVE_SA_JSON"] == "from-toolhive"
    assert os.environ["GOOGLE_DRIVE_FOLDER_ID"] == "folder-from-file"


def test_load_env_missing_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orphan = tmp_path / "orphan" / "pkg"
    orphan.mkdir(parents=True)
    main_path = orphan / "__main__.py"
    main_path.write_text(_main_py(connector_id="x"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("orphan_main", main_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit, match="cannot locate generated MCP project root"):
        mod._load_env()
