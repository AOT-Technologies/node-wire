#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for scripts/meta_local_deps.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "meta_local_deps.py"


def _run(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _package(root: Path, rel: str, name: str, deps: list[str] | None = None) -> None:
    pkg = root / rel
    pkg.mkdir(parents=True)
    lines = ["[project]", f'name = "{name}"', 'version = "1.0.0"']
    if deps is not None:
        lines.append("dependencies = [" + ", ".join(f'"{d}"' for d in deps) + "]")
    (pkg / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fake_repo(root: Path, meta_deps: list[str]) -> Path:
    _package(root, "packages/runtime", "node-wire-runtime")
    _package(root, "packages/connectors/http_generic", "node-wire-http")
    _package(root, "packages/connectors/google_drive", "node_wire.Google_Drive")
    _package(root, "packages/meta", "node-wire-connectors", meta_deps)
    return root


def test_real_repo_covers_every_meta_dependency() -> None:
    proc = _run(REPO_ROOT)
    assert proc.returncode == 0, proc.stderr
    paths = proc.stdout.split()
    assert paths[0] == "packages/runtime"
    assert len(paths) == 9
    assert all((REPO_ROOT / p / "pyproject.toml").is_file() for p in paths)


def test_maps_names_in_meta_order_and_skips_third_party(tmp_path: Path) -> None:
    repo = _fake_repo(
        tmp_path,
        [
            "node-wire-http>=1.0.0b1,<2",
            "requests>=2",
            "node-wire-google-drive[extra]>=1.0; python_version >= '3.11'",
            "node-wire-runtime>=1.1,<2",
        ],
    )
    proc = _run(repo)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == [
        "packages/connectors/http_generic",
        "packages/connectors/google_drive",
        "packages/runtime",
    ]


def test_fails_when_meta_dependency_has_no_local_package(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path, ["node-wire-http>=1.0", "node-wire-jira>=1.0"])
    proc = _run(repo)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "node-wire-jira" in proc.stderr


def test_fails_when_two_packages_share_a_name(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path, ["node-wire-http>=1.0"])
    _package(tmp_path, "packages/connectors/http_copy", "node-wire-http")
    proc = _run(repo)
    assert proc.returncode == 1
    assert "node-wire-http" in proc.stderr
