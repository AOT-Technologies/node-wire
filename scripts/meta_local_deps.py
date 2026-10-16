#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Print the local package path for each node-wire-* dependency of the meta package.

CI installs these from the checkout instead of PyPI, where the needed version
(or a cp313 wheel) may not be published yet. Deriving the list from
packages/meta/pyproject.toml keeps workflows in sync with it. Exits 1 if a
dependency has no matching package under packages/runtime or
packages/connectors/*, so it can't silently fall through to PyPI.
"""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

_REQUIREMENT_NAME = re.compile(r"[A-Za-z0-9._-]+")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dist_name(package_dir: Path) -> str:
    with (package_dir / "pyproject.toml").open("rb") as f:
        return _normalize(tomllib.load(f)["project"]["name"])


def local_deps(repo_root: Path) -> list[str]:
    candidates = [repo_root / "packages" / "runtime"] + sorted(
        p.parent for p in (repo_root / "packages" / "connectors").glob("*/pyproject.toml")
    )
    paths: dict[str, Path] = {}
    for package_dir in candidates:
        name = _dist_name(package_dir)
        if name in paths:
            raise SystemExit(f"ERROR: {name} is declared by both {paths[name]} and {package_dir}")
        paths[name] = package_dir

    with (repo_root / "packages" / "meta" / "pyproject.toml").open("rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    ours = []
    for dep in deps:
        match = _REQUIREMENT_NAME.match(dep)
        if match is None:
            raise SystemExit(f"ERROR: unparseable meta dependency: {dep!r}")
        name = _normalize(match.group())
        if name.startswith("node-wire-"):
            ours.append(name)

    missing = [name for name in ours if name not in paths]
    if missing:
        raise SystemExit(f"ERROR: no local package for meta deps: {missing}")
    return [paths[name].relative_to(repo_root).as_posix() for name in ours]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    for path in local_deps(args.repo_root.resolve()):
        print(path)


if __name__ == "__main__":
    main()
