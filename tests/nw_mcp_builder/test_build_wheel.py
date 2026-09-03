# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for _build_wheel (mocked subprocess)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nw_mcp_builder.from_connector import _build_wheel, build_connector_wheels


def test_build_wheel_uvx_success(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    whl = pkg / "dist" / "pkg-0.1-py3-none-any.whl"

    def fake_run(cmd, **kwargs):
        whl.parent.mkdir(parents=True, exist_ok=True)
        whl.write_bytes(b"whl")
        return MagicMock(returncode=0)

    with patch("nw_mcp_builder.from_connector.subprocess.run", side_effect=fake_run) as run:
        result = _build_wheel(pkg, python="3.12")
    assert result == whl
    assert run.call_args.args[0][0] == "uvx"
    assert run.call_args.kwargs["env"]["UV_PYTHON"] == "3.12"


def test_build_wheel_pip_fallback_when_uvx_missing(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    whl = pkg / "dist" / "pkg-0.1-py3-none-any.whl"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "uvx":
            raise FileNotFoundError("uvx")
        if cmd[0] == "python" and "pip" in cmd:
            return MagicMock(returncode=0)
        if "build" in cmd:
            whl.parent.mkdir(parents=True, exist_ok=True)
            whl.write_bytes(b"whl")
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("nw_mcp_builder.from_connector.subprocess.run", side_effect=fake_run):
        result = _build_wheel(pkg)
    assert result == whl
    assert calls[0][0] == "uvx"
    assert any("pip" in c for c in calls)


def test_build_wheel_raises_on_called_process_error(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    err = subprocess.CalledProcessError(1, ["uvx"], stderr="build failed hard")
    with patch("nw_mcp_builder.from_connector.subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="Wheel build failed"):
            _build_wheel(pkg)


def test_build_wheel_raises_when_no_whl(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    with patch("nw_mcp_builder.from_connector.subprocess.run", return_value=MagicMock()):
        with pytest.raises(FileNotFoundError, match="No .whl"):
            _build_wheel(pkg)


def test_build_connector_wheels_calls_both(tmp_path: Path) -> None:
    runtime = tmp_path / "packages" / "runtime"
    connector = tmp_path / "packages" / "connectors" / "demo"
    runtime.mkdir(parents=True)
    connector.mkdir(parents=True)
    r_whl = runtime / "dist" / "r.whl"
    c_whl = connector / "dist" / "c.whl"

    def fake_build(package_dir: Path, *, python: str | None = None) -> Path:
        if package_dir == runtime:
            r_whl.parent.mkdir(parents=True, exist_ok=True)
            r_whl.write_bytes(b"r")
            return r_whl
        c_whl.parent.mkdir(parents=True, exist_ok=True)
        c_whl.write_bytes(b"c")
        return c_whl

    with patch("nw_mcp_builder.from_connector._build_wheel", side_effect=fake_build):
        out = build_connector_wheels(tmp_path, "demo")
    assert out == (r_whl, c_whl)
