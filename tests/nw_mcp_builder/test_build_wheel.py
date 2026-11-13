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


def _fake_builder(built: list[str]):
    def fake_build(package_dir: Path, *, python: str | None = None) -> Path:
        built.append(package_dir.name)
        whl = package_dir / "dist" / f"{package_dir.name}-{len(built)}-py3-none-any.whl"
        whl.parent.mkdir(parents=True, exist_ok=True)
        for old in whl.parent.glob("*.whl"):
            old.unlink()
        whl.write_bytes(b"w")
        return whl

    return fake_build


def _packages(root: Path) -> None:
    for pkg in ("runtime", "bindings", "connectors/demo"):
        (root / "packages" / pkg).mkdir(parents=True)
        (root / "packages" / pkg / "setup.py").write_text(pkg, encoding="utf-8")


def test_build_connector_wheels_returns_the_three_built_wheels(tmp_path: Path) -> None:
    _packages(tmp_path)
    built: list[str] = []
    with patch("nw_mcp_builder.from_connector._build_wheel", side_effect=_fake_builder(built)):
        out = build_connector_wheels(tmp_path, "demo")
    assert [w.name for w in out] == [
        "runtime-1-py3-none-any.whl",
        "bindings-2-py3-none-any.whl",
        "demo-3-py3-none-any.whl",
    ]


def test_wheels_with_unchanged_sources_are_reused(tmp_path: Path) -> None:
    """One stamp per package, whatever interpreter builds it: the wheels are pure Python."""
    _packages(tmp_path)
    built: list[str] = []
    with patch("nw_mcp_builder.from_connector._build_wheel", side_effect=_fake_builder(built)):
        first = build_connector_wheels(tmp_path, "demo")
        second = build_connector_wheels(tmp_path, "demo", python="3.12")
        assert built == ["runtime", "bindings", "demo"]
        assert second == first

        (tmp_path / "packages" / "runtime" / "setup.py").write_text("changed", encoding="utf-8")
        build_connector_wheels(tmp_path, "demo")
    assert built[3:] == ["runtime"]


def test_a_failed_build_keeps_the_wheels_already_stamped(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import WheelState, wheel_state

    _packages(tmp_path)
    built: list[str] = []
    ok = _fake_builder(built)

    def fail_on_bindings(package_dir: Path, *, python: str | None = None) -> Path:
        if package_dir.name == "bindings":
            raise RuntimeError("Wheel build failed")
        return ok(package_dir, python=python)

    with (
        patch("nw_mcp_builder.from_connector._build_wheel", side_effect=fail_on_bindings),
        pytest.raises(RuntimeError),
    ):
        build_connector_wheels(tmp_path, "demo")
    assert wheel_state(tmp_path, "packages/runtime") is WheelState.FRESH
    assert wheel_state(tmp_path, "packages/bindings") is WheelState.MISSING
