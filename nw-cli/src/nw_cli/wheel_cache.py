# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Source stamps that let a wheel build be skipped while a package's sources are unchanged.

A stamp ``<package>/dist/.nw-source-<key>.sha256`` holds the hash of the sources the wheels were
built from, then the names of the wheels that build wrote. ``key`` is the build flavour (a
``build-packages.sh`` mode such as ``linux-only``, or ``cp313-musllinux`` for stacklok images).
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

_HASH_SKIP_DIRS = {"__pycache__", "build", "dist"}


def package_source_hash(node_wire_root: Path, package: str) -> str:
    """Hash of everything a package's wheel is compiled from.

    That is the package folder (pyproject, setup.py, README) and the ``src/`` packages its
    ``[tool.setuptools.packages.find]`` includes.
    """
    pkg_dir = node_wire_root / package
    pyproject = pkg_dir / "pyproject.toml"
    find = (
        (tomllib.loads(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else {})
        .get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    roots = [pkg_dir]
    for where in find.get("where", []):
        for include in find.get("include", []):
            top = include.split(".", 1)[0].rstrip("*")
            roots.append((pkg_dir / where / top).resolve())
    digest = hashlib.sha256()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if not path.is_file() or _HASH_SKIP_DIRS & set(rel.parts):
                continue
            if path.suffix in {".so", ".pyd", ".c", ".pyc"} or ".egg-info" in str(rel):
                continue
            if path.name == "report.json":  # build report; varies per run, not in the wheel
                continue
            digest.update(f"{root.name}/{rel.as_posix()}\0".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def stamp_path(node_wire_root: Path, package: str, key: str) -> Path:
    return node_wire_root / package / "dist" / f".nw-source-{key}.sha256"


def is_fresh(node_wire_root: Path, package: str, key: str) -> bool:
    """True when the stamped build matches the sources and its wheels are all still there."""
    stamp = stamp_path(node_wire_root, package, key)
    if not stamp.is_file():
        return False
    parts = stamp.read_text(encoding="utf-8").split()
    if not parts:
        return False
    digest, *wheels = parts
    dist = stamp.parent
    if not wheels or not all((dist / w).is_file() for w in wheels):
        return False
    return digest == package_source_hash(node_wire_root, package)


def record_build(node_wire_root: Path, package: str, key: str, *, since: float) -> None:
    """Stamp ``package`` with its source hash and the wheels written after ``since``."""
    dist = node_wire_root / package / "dist"
    wheels = sorted(w.name for w in dist.glob("*.whl") if w.stat().st_mtime >= since - 1)
    stamp = stamp_path(node_wire_root, package, key)
    if not wheels:
        stamp.unlink(missing_ok=True)
        return
    stamp.write_text(
        "\n".join([package_source_hash(node_wire_root, package), *wheels]) + "\n",
        encoding="utf-8",
    )
