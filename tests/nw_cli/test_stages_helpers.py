# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for nw_cli stage helpers (no Docker / build-packages)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nw_cli.stages import (
    StageError,
    build_mode_flag,
    connector_wheel_present,
    register_all_packages,
    run_docker_build,
    run_wheel_build,
    runtime_wheel_present,
    wheels_present,
)


def test_build_mode_flag_defaults_and_mutex() -> None:
    assert build_mode_flag() == "--linux-only"
    assert build_mode_flag(host=True) == "--host-only"
    assert build_mode_flag(all_=True) == "--all"
    with pytest.raises(StageError, match="mutually exclusive"):
        build_mode_flag(host=True, all_=True)


def test_wheels_present_helpers(tmp_path: Path) -> None:
    runtime = tmp_path / "packages" / "runtime" / "dist"
    conn = tmp_path / "packages" / "connectors" / "pet_store" / "dist"
    runtime.mkdir(parents=True)
    conn.mkdir(parents=True)
    assert wheels_present(tmp_path, "pet_store") is False
    assert runtime_wheel_present(tmp_path) is False
    assert connector_wheel_present(tmp_path, "pet_store") is False

    (runtime / "runtime-0.1-py3-none-any.whl").write_bytes(b"whl")
    assert runtime_wheel_present(tmp_path) is True
    assert wheels_present(tmp_path, "pet_store") is False

    (conn / "pet_store-0.1-py3-none-any.whl").write_bytes(b"whl")
    assert connector_wheel_present(tmp_path, "pet_store") is True
    assert wheels_present(tmp_path, "pet_store") is True


def test_run_wheel_build_requires_connector_or_runtime(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="--connector-id"):
        run_wheel_build(tmp_path)


def test_run_wheel_build_missing_script(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="build-packages.sh not found"):
        run_wheel_build(tmp_path, connector_id="pet_store")


def test_run_wheel_build_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "build-packages.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    with patch("nw_cli.stages.run_logged_command", return_value=7):
        with pytest.raises(StageError, match="exit 7"):
            run_wheel_build(tmp_path, connector_id="pet_store")


def test_run_docker_build_missing_project(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="MCP project directory not found"):
        run_docker_build(tmp_path, "pet_store")


def test_run_docker_build_success(tmp_path: Path) -> None:
    project = tmp_path / "nw-mcp-builder" / "out" / "pet-store-nw-mcp"
    project.mkdir(parents=True)
    with patch("nw_cli.stages.run_logged_command", return_value=0) as run:
        image = run_docker_build(tmp_path, "pet_store", tag="v1")
    assert image == "pet-store-nw-mcp:v1"
    assert run.call_args.args[0] == ["docker", "build", "-t", "pet-store-nw-mcp:v1", "."]
    assert run.call_args.kwargs["cwd"] == project


def test_register_all_packages_inserts_once(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "build-packages.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        "ALL_PACKAGES=(\n  packages/runtime\n  packages/connectors/slack\n)\n",
        encoding="utf-8",
    )
    assert register_all_packages(tmp_path, "pet_store") is True
    text = script.read_text(encoding="utf-8")
    assert "packages/connectors/pet_store" in text
    assert register_all_packages(tmp_path, "pet_store") is False


def test_register_all_packages_missing_block(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "build-packages.sh"
    script.parent.mkdir(parents=True)
    script.write_text("echo hi\n", encoding="utf-8")
    with pytest.raises(StageError, match="ALL_PACKAGES"):
        register_all_packages(tmp_path, "pet_store")


def test_run_logged_command_streams_to_log(tmp_path: Path) -> None:
    from nw_cli.stages import run_logged_command

    lines: list[str] = []
    proc = MagicMock()
    proc.stdout = iter(["hello\n", "world\n"])
    proc.wait.return_value = 0
    with patch("nw_cli.stages.subprocess.Popen", return_value=proc):
        code = run_logged_command(["echo"], cwd=tmp_path, log=lines.append)
    assert code == 0
    assert lines == ["hello", "world"]
