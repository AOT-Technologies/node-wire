#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""from_connector unit tests (no real wheel builds)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nw_mcp_builder.from_connector import (
    action_to_tool_name,
    discover_actions,
    discover_secret_env_names,
    ensure_connector_fixture,
    format_success_message,
    run_from_connector,
    write_env_example,
)


def test_invalid_connector_id(fake_node_wire: Path, package_root: Path) -> None:
    with pytest.raises(ValueError, match="Invalid connector_id"):
        run_from_connector(
            "GoogleDrive",
            node_wire_root=fake_node_wire,
            package_root=package_root,
            skip_build_wheels=True,
        )


def test_validate_layout_missing_logic(tmp_path: Path, package_root: Path) -> None:
    root = tmp_path / "nw"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname="x"\n', encoding="utf-8")
    pkg = root / "packages" / "connectors" / "demo_conn"
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="logic.py"):
        run_from_connector(
            "demo_conn",
            node_wire_root=root,
            package_root=package_root,
            skip_build_wheels=True,
        )


def test_discover_actions_and_tool_names(fake_node_wire: Path) -> None:
    logic = fake_node_wire / "src" / "node_wire_demo_conn" / "logic.py"
    actions = discover_actions(logic)
    assert actions == ["ping", "files.list"]
    assert action_to_tool_name("files.list") == "files_list"
    assert action_to_tool_name("ping") == "ping"


def test_discover_actions_sdk_spec_assign(tmp_path: Path) -> None:
    pkg = tmp_path / "node_wire_x"
    pkg.mkdir()
    (pkg / "logic.py").write_text(
        'SPECS["files.create"] = SdkActionSpec(\n    "x"\n)\n',
        encoding="utf-8",
    )
    assert discover_actions(pkg / "logic.py") == ["files.create"]


def test_ensure_connector_fixture_creates_and_skips(
    fake_node_wire: Path, package_root: Path
) -> None:
    fixtures = package_root / "fixtures"
    path = ensure_connector_fixture(
        "demo_conn",
        node_wire_root=fake_node_wire,
        fixtures_dir=fixtures,
    )
    assert path.is_file()
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["runtime"]["connector_id"] == "demo_conn"
    assert doc["server"]["name"] == "demo-conn-nw"
    tool_names = {t["tool_name"] for t in doc["groups"][0]["tools"]}
    assert tool_names == {"ping", "files_list"}

    mtime = path.stat().st_mtime_ns
    path2 = ensure_connector_fixture(
        "demo_conn",
        node_wire_root=fake_node_wire,
        fixtures_dir=fixtures,
        force=False,
    )
    assert path2 == path
    assert path.stat().st_mtime_ns == mtime

    ensure_connector_fixture(
        "demo_conn",
        node_wire_root=fake_node_wire,
        fixtures_dir=fixtures,
        force=True,
    )
    assert path.is_file()


def test_discover_secrets_and_env_example(fake_node_wire: Path, tmp_path: Path) -> None:
    names = discover_secret_env_names("demo_conn", fake_node_wire)
    assert "DEMO_CONN_SA_JSON" in names
    assert "DEMO_CONN_TOKEN" in names

    project = tmp_path / "host"
    project.mkdir()
    example = write_env_example(project, "demo_conn", fake_node_wire)
    text = example.read_text(encoding="utf-8")
    assert "NW_ALLOWED_CONNECTORS=demo_conn" in text
    assert "DEMO_CONN_SA_JSON=" in text
    assert "NW_MCP_AUTH_DISABLED=true" in text


def test_run_from_connector_skip_wheels_generates_project(
    fake_node_wire: Path, package_root: Path
) -> None:
    project_dir = run_from_connector(
        "demo_conn",
        node_wire_root=fake_node_wire,
        package_root=package_root,
        skip_build_wheels=True,
    )
    assert project_dir.name == "demo-conn-nw-mcp"
    assert (project_dir / "src" / "demo_conn_nw_mcp" / "__main__.py").is_file()
    assert (project_dir / ".env.example").is_file()
    assert (project_dir / "wheels").is_dir()
    assert list((project_dir / "wheels").glob("*.whl"))
    assert (project_dir / "vendor" / "node_wire_src" / "bindings").is_dir()
    assert (project_dir / "Dockerfile").is_file()

    with pytest.raises(FileExistsError, match="already exists"):
        run_from_connector(
            "demo_conn",
            node_wire_root=fake_node_wire,
            package_root=package_root,
            skip_build_wheels=True,
        )

    again = run_from_connector(
        "demo_conn",
        node_wire_root=fake_node_wire,
        package_root=package_root,
        skip_build_wheels=True,
        force_output=True,
    )
    assert again == project_dir


def test_run_from_connector_requires_wheels_when_skip(
    fake_node_wire: Path, package_root: Path
) -> None:
    for whl in (fake_node_wire / "packages" / "runtime" / "dist").glob("*.whl"):
        whl.unlink()
    with pytest.raises(FileNotFoundError, match="runtime wheel"):
        run_from_connector(
            "demo_conn",
            node_wire_root=fake_node_wire,
            package_root=package_root,
            skip_build_wheels=True,
        )


def test_format_success_message(tmp_path: Path) -> None:
    project = tmp_path / "google-drive-nw-mcp"
    mod = project / "src" / "google_drive_nw_mcp"
    mod.mkdir(parents=True)
    (mod / "__main__.py").write_text("# stub\n", encoding="utf-8")
    msg = format_success_message(project, "google_drive")
    assert "google_drive" in msg
    assert "google_drive_nw_mcp" in msg


def test_build_wheels_invoked_when_not_skipped(fake_node_wire: Path, package_root: Path) -> None:
    with patch("nw_mcp_builder.from_connector.build_connector_wheels") as build:
        build.return_value = (
            fake_node_wire / "packages" / "runtime" / "dist" / "x.whl",
            fake_node_wire / "packages" / "connectors" / "demo_conn" / "dist" / "y.whl",
        )
        run_from_connector(
            "demo_conn",
            node_wire_root=fake_node_wire,
            package_root=package_root,
            skip_build_wheels=False,
            python="3.12",
        )
        build.assert_called_once()
        assert build.call_args.kwargs["python"] == "3.12"
