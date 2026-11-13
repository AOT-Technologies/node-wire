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
