# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Wheel source stamps: the pruned walk hashes exactly what the original ``rglob`` walk did."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from nw_mcp_builder import wheel_cache
from nw_mcp_builder.wheel_cache import package_source_hash

ROOT = Path(__file__).resolve().parents[2]


def _reference_hash(node_wire_root: Path, package: str) -> str:
    """The original implementation (full ``rglob``), kept to pin the digest."""
    pkg_dir = node_wire_root / package
    roots = [pkg_dir]
    find = wheel_cache.tomllib.loads((pkg_dir / "pyproject.toml").read_text(encoding="utf-8"))
    find = find.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    for where in find.get("where", []):
        for include in find.get("include", []):
            roots.append((pkg_dir / where / include.split(".", 1)[0].rstrip("*")).resolve())
    digest = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if not path.is_file() or {"__pycache__", "build", "dist"} & set(rel.parts):
                continue
            if path.suffix in {".so", ".pyd", ".c", ".pyc"} or ".egg-info" in str(rel):
                continue
            if path.name == "report.json":
                continue
            digest.update(f"{root.name}/{rel.as_posix()}\0".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize("package", ["packages/runtime", "packages/bindings"])
def test_digest_matches_original_walk_on_real_packages(package: str) -> None:
    assert package_source_hash(ROOT, package) == _reference_hash(ROOT, package)


def test_digest_matches_original_walk_with_build_artifacts(tmp_path: Path) -> None:
    pkg = tmp_path / "packages" / "demo"
    (pkg / "dist").mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[tool.setuptools.packages.find]\nwhere = ["../../src"]\ninclude = ["demo*"]\n',
        encoding="utf-8",
    )
    src = tmp_path / "src" / "demo"
    for rel in [
        "__init__.py",
        "a/b.py",
        "a/b.c",
        "build/x.py",
        "x.egg-info/PKG-INFO",
        "__pycache__/m.pyc",
        "report.json",
        "sub/dist/skipped.py",
    ]:
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(rel, encoding="utf-8")
    (pkg / "dist" / "w.whl").write_text("w", encoding="utf-8")
    (pkg / "build" / "lib").mkdir(parents=True)
    (pkg / "build" / "lib" / "big.py").write_text("x", encoding="utf-8")

    assert package_source_hash(tmp_path, "packages/demo") == _reference_hash(
        tmp_path, "packages/demo"
    )


# --- one wheel-state function (MISSING / STALE / FRESH) -----------------------------------


def _package(root: Path, name: str = "packages/demo") -> Path:
    pkg = root / name
    (pkg / "dist").mkdir(parents=True, exist_ok=True)
    (pkg / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    return pkg


def _wheel(pkg: Path, name: str = "demo-1.0-py3-none-any.whl") -> Path:
    wheel = pkg / "dist" / name
    wheel.write_bytes(b"w")
    return wheel


def test_state_is_missing_without_a_wheel(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import WheelState, wheel_state

    _package(tmp_path)
    assert wheel_state(tmp_path, "packages/demo") is WheelState.MISSING


def test_an_unstamped_wheel_is_stale_not_fresh(tmp_path: Path) -> None:
    """A wheel nobody stamped (built by hand, or before this check) cannot be trusted."""
    from nw_mcp_builder.wheel_cache import WheelState, wheel_state

    _wheel(_package(tmp_path))
    assert wheel_state(tmp_path, "packages/demo") is WheelState.STALE


def test_stamped_wheel_is_fresh_until_the_sources_change(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import (
        WheelState,
        fresh_wheel,
        package_source_hash,
        stamp_build,
        wheel_state,
    )

    pkg = _package(tmp_path)
    digest = package_source_hash(tmp_path, "packages/demo")
    wheel = _wheel(pkg)
    assert stamp_build(tmp_path, "packages/demo", digest) == wheel
    assert wheel_state(tmp_path, "packages/demo") is WheelState.FRESH
    assert fresh_wheel(tmp_path, "packages/demo") == wheel

    (pkg / "pyproject.toml").write_text('[project]\nname = "demo2"\n', encoding="utf-8")
    assert wheel_state(tmp_path, "packages/demo") is WheelState.STALE


def test_sources_are_hashed_before_the_build(tmp_path: Path) -> None:
    """An edit made while the wheel builds must leave the wheel stale, not stamp it fresh."""
    from nw_mcp_builder.wheel_cache import (
        WheelState,
        package_source_hash,
        stamp_build,
        wheel_state,
    )

    pkg = _package(tmp_path)
    before = package_source_hash(tmp_path, "packages/demo")
    _wheel(pkg)
    (pkg / "pyproject.toml").write_text('[project]\nname = "edited"\n', encoding="utf-8")
    stamp_build(tmp_path, "packages/demo", before)
    assert wheel_state(tmp_path, "packages/demo") is WheelState.STALE


def test_a_compiled_wheel_is_removed_not_stamped(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import (
        WheelState,
        package_source_hash,
        stamp_build,
        wheel_state,
    )

    pkg = _package(tmp_path)
    digest = package_source_hash(tmp_path, "packages/demo")
    compiled = _wheel(pkg, "demo-1.0-cp313-cp313-linux_x86_64.whl")
    with pytest.raises(FileNotFoundError, match="py3-none-any"):
        stamp_build(tmp_path, "packages/demo", digest)
    assert not compiled.exists()
    assert wheel_state(tmp_path, "packages/demo") is WheelState.MISSING


def test_a_deleted_stamped_wheel_is_missing(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import (
        WheelState,
        package_source_hash,
        stamp_build,
        wheel_state,
    )

    pkg = _package(tmp_path)
    digest = package_source_hash(tmp_path, "packages/demo")
    wheel = _wheel(pkg)
    stamp_build(tmp_path, "packages/demo", digest)
    wheel.unlink()
    assert wheel_state(tmp_path, "packages/demo") is WheelState.MISSING


def test_fresh_wheels_names_every_package_that_is_not_fresh(tmp_path: Path) -> None:
    from nw_mcp_builder.wheel_cache import WheelsNotFreshError, fresh_wheels

    _package(tmp_path, "packages/a")
    _wheel(_package(tmp_path, "packages/b"))
    with pytest.raises(WheelsNotFreshError) as excinfo:
        fresh_wheels(tmp_path, ["packages/a", "packages/b"])
    assert excinfo.value.packages == {"packages/a": "missing", "packages/b": "stale"}
    assert "packages/a (missing)" in str(excinfo.value)
