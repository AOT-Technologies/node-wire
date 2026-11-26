# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Wheel state: is a package's wheel in ``dist/`` built from its current sources?

:func:`wheel_state` is the only check (``MISSING`` / ``STALE`` / ``FRESH``); every builder and
generator asks it, and :func:`fresh_wheels` hands the resolved wheel paths on, so nothing picks
"the newest wheel in dist/" by itself.

A stamp ``<package>/dist/.nw-source-py3-none-any.sha256`` holds the hash of the sources the wheel
was built from (taken *before* the build, so an edit during the build leaves it stale), then the
name of the one pure-Python wheel that build wrote. Stamps under any other key (the old
``mcp-host-<interpreter>`` ones) are never read.
"""

from __future__ import annotations

import hashlib
import os
import tomllib
from collections.abc import Iterable
from enum import Enum
from pathlib import Path

# Every package builds one pure-Python wheel, whoever builds it (nw gen-whl, nw-mcp-builder).
WHEEL_STAMP_KEY = "py3-none-any"


class WheelState(Enum):
    MISSING = "missing"  # no wheel to use
    STALE = "stale"  # a wheel, but not stamped as built from the current sources
    FRESH = "fresh"  # the stamped wheel, built from the current sources


class WheelsNotFreshError(FileNotFoundError):
    """Packages whose wheel is missing or stale; ``packages`` maps each to its state."""

    def __init__(self, packages: dict[str, str]) -> None:
        self.packages = packages
        listed = ", ".join(f"{p} ({state})" for p, state in packages.items())
        super().__init__(f"Wheels not built from the current sources: {listed}")


_HASH_SKIP_DIRS = {"__pycache__", "build", "dist"}


def package_source_hash(node_wire_root: Path, package: str) -> str:
    """Hash of everything a package's wheel is built from.

    That is the package folder (pyproject, README) and the ``src/`` packages its
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
        for path in sorted(_source_files(root)):
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


def _source_files(root: Path) -> list[Path]:
    """Every entry under ``root`` except skipped folders, which aren't descended into.

    Same set the hash loop would keep from ``root.rglob("*")``, without walking ``build/``
    (most of a built package's files).
    """
    found: list[Path] = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _HASH_SKIP_DIRS and ".egg-info" not in d]
        base = Path(dirpath)
        found.extend(base / name for name in files)
    return found


def stamp_path(node_wire_root: Path, package: str) -> Path:
    return node_wire_root / package / "dist" / f".nw-source-{WHEEL_STAMP_KEY}.sha256"


def is_pure_python_wheel(wheel: Path) -> bool:
    """True for a ``py3-none-any`` wheel: it installs on any image whatever its Python or CPU."""
    parts = wheel.name[: -len(".whl")].split("-")
    if not wheel.name.endswith(".whl") or len(parts) < 5:
        return False
    python_tags, abi_tags, platform_tags = (tag.split(".") for tag in parts[-3:])
    return "py3" in python_tags and "none" in abi_tags and "any" in platform_tags


def _stamped(node_wire_root: Path, package: str) -> tuple[str, Path] | None:
    stamp = stamp_path(node_wire_root, package)
    if not stamp.is_file():
        return None
    parts = stamp.read_text(encoding="utf-8").split()
    if len(parts) != 2:  # an older multi-wheel stamp, or a damaged one
        return None
    return parts[0], stamp.parent / parts[1]


def wheel_state(node_wire_root: Path, package: str) -> WheelState:
    """``FRESH`` only for the stamped wheel of a build from the current sources."""
    stamped = _stamped(node_wire_root, package)
    if stamped is None or not stamped[1].is_file():
        dist = node_wire_root / package / "dist"
        has_wheel = dist.is_dir() and any(is_pure_python_wheel(w) for w in dist.glob("*.whl"))
        return WheelState.STALE if has_wheel else WheelState.MISSING
    digest, _wheel = stamped
    if digest != package_source_hash(node_wire_root, package):
        return WheelState.STALE
    return WheelState.FRESH


def fresh_wheel(node_wire_root: Path, package: str) -> Path:
    """The stamped wheel of ``package``; raises :class:`WheelsNotFreshError` unless ``FRESH``."""
    return fresh_wheels(node_wire_root, [package])[package]


def fresh_wheels(node_wire_root: Path, packages: Iterable[str]) -> dict[str, Path]:
    """``{package: wheel}`` for packages that are all ``FRESH``; else one error naming the rest."""
    found: dict[str, Path] = {}
    problems: dict[str, str] = {}
    for package in packages:
        state = wheel_state(node_wire_root, package)
        if state is WheelState.FRESH:
            stamped = _stamped(node_wire_root, package)
            assert stamped is not None
            found[package] = stamped[1]
        else:
            problems[package] = state.value
    if problems:
        raise WheelsNotFreshError(problems)
    return found


def stamp_build(node_wire_root: Path, package: str, digest: str) -> Path:
    """Stamp the wheel a just-finished build wrote; ``digest`` was hashed before the build.

    The build must have left exactly one pure-Python wheel in ``dist/``. A compiled wheel is
    removed (it would only install on the platform it was built for), and nothing is stamped.
    """
    dist = node_wire_root / package / "dist"
    stamp = stamp_path(node_wire_root, package)
    stamp.unlink(missing_ok=True)
    wheels = sorted(dist.glob("*.whl")) if dist.is_dir() else []
    pure = [w for w in wheels if is_pure_python_wheel(w)]
    for wheel in wheels:
        if wheel not in pure:
            wheel.unlink()
    if len(pure) != 1:
        found = ", ".join(w.name for w in wheels) or "none"
        raise FileNotFoundError(
            f"Expected one py3-none-any wheel in {dist} after the build; found: {found}"
        )
    stamp.write_text(f"{digest}\n{pure[0].name}\n", encoding="utf-8")
    return pure[0]
