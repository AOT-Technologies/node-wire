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


def test_load_env_skips_dotenv_file_in_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "host"
    mod = _load_generated_module(project, "google_drive")
    (project / ".env").write_text(
        "GOOGLE_DRIVE_SA_JSON=from-file\nLEAKED_SECRET=should-not-load\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NW_MCP_CONTAINER", "true")
    monkeypatch.delenv("GOOGLE_DRIVE_SA_JSON", raising=False)
    monkeypatch.delenv("LEAKED_SECRET", raising=False)

    mod._load_env()
    assert os.environ["NW_REST_LOAD_DOTENV"] == "false"
    assert "GOOGLE_DRIVE_SA_JSON" not in os.environ
    assert "LEAKED_SECRET" not in os.environ


def test_server_mode_is_the_default_outside_a_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No container detected is not a reason to turn auth off (D2)."""
    for name in ("NW_MCP_LOCAL_DEV", "NW_MCP_CONTAINER", "NW_MCP_AUTH_DISABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NW_MCP_SCOPE_POLICY_DEFAULT", raising=False)
    mod = _load_generated_module(tmp_path / "host", "smtp")
    assert not mod._running_in_container()

    mode = mod._run_mode()
    mod._apply_run_mode(mode)

    assert mode == "server"
    assert "NW_MCP_AUTH_DISABLED" not in os.environ
    assert "NW_MCP_SCOPE_POLICY_DEFAULT" not in os.environ
    assert "auth enforced" in mod._describe_run_mode(mode)


def test_local_dev_mode_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_LOCAL_DEV", "true")
    monkeypatch.delenv("NW_MCP_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_MCP_SCOPE_POLICY_DEFAULT", raising=False)
    mod = _load_generated_module(tmp_path / "host", "smtp")

    mode = mod._run_mode()
    mod._apply_run_mode(mode)

    assert mode == "local-dev"
    assert os.environ["NW_MCP_AUTH_DISABLED"] == "true"
    assert os.environ["NW_MCP_SCOPE_POLICY_DEFAULT"] == "allow"
    assert "auth DISABLED" in mod._describe_run_mode(mode)


def test_container_detection_never_decides_auth() -> None:
    src = _main_py(connector_id="smtp")
    body = src[src.index("def main() -> None:") :]
    assert "_running_in_container" not in body
    assert body.index("_load_env()") < body.index("mode = _run_mode()")
    assert "_describe_run_mode(mode)" in body
    assert src.index("if _running_in_container():") < src.index("from dotenv import load_dotenv")


def test_generated_main_configures_host_logging_after_env_and_before_the_server() -> None:
    """Runtime taxonomy/trace fields reach the console; OTLP settings may come from .env."""
    src = _main_py(connector_id="smtp")
    call = 'configure_host_logging("nw-smtp")'
    assert "from node_wire_runtime.host_logging import configure_host_logging" in src
    assert src.index("_load_env()\n") < src.index(call) < src.index("McpServer(\n")


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


def test_load_env_container_without_project_root_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the Dockerfile never COPYs pyproject.toml, so _project_root()
    heuristics never match inside a real container. _load_env() must not treat
    that as fatal — it crash-looped every generated image with
    "auth error: cannot locate generated MCP project root" until container mode
    was checked first.
    """
    orphan = tmp_path / "app" / "src" / "pkg"
    orphan.mkdir(parents=True)
    main_path = orphan / "__main__.py"
    main_path.write_text(_main_py(connector_id="x"), encoding="utf-8")
    monkeypatch.setenv("NW_MCP_CONTAINER", "true")
    spec = importlib.util.spec_from_file_location("container_orphan_main", main_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._load_env()  # must not raise
    assert os.environ["NW_REST_LOAD_DOTENV"] == "false"
