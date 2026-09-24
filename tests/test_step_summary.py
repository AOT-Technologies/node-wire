#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for .github/scripts/step_summary.sh."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "step_summary.sh"


def _bash() -> str:
    """Locate a bash that can actually run the script.

    A bare "bash" on Windows resolves to the WSL launcher in System32, which
    exits 1 without reading the script when no distribution is installed.
    GitHub Actions' own `shell: bash` points at Git for Windows, so match it.
    """
    if sys.platform != "win32":
        return "bash"
    roots = (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files")
    for root in roots:
        if root and (candidate := Path(root) / "Git" / "bin" / "bash.exe").is_file():
            return str(candidate)
    pytest.skip("Git for Windows bash is not installed")


def _run(tmp_path: Path, *args: str, stdin: str | None = None, summary: bool = True) -> str:
    dest = tmp_path / "summary.md"
    env = os.environ.copy()
    if summary:
        # as_posix() keeps the drive-letter path digestible to Git Bash, which
        # would otherwise see the backslashes as escapes inside the redirect.
        env["GITHUB_STEP_SUMMARY"] = dest.as_posix()
    else:
        env.pop("GITHUB_STEP_SUMMARY", None)
    result = subprocess.run(
        [_bash(), str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        input=stdin,
        text=True,
        env=env,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    return dest.read_text(encoding="utf-8") if dest.is_file() else ""


def test_appends_each_argument_as_a_line(tmp_path: Path) -> None:
    text = _run(tmp_path, "### DCO", "Bot author is exempt.")
    assert text == "### DCO\nBot author is exempt.\n"


def test_fence_wraps_stdin(tmp_path: Path) -> None:
    text = _run(tmp_path, "--fence", "### Diff coverage", stdin="line one\n")
    assert text == "### Diff coverage\n\n```\nline one\n```\n"


def test_missing_summary_file_is_a_noop(tmp_path: Path) -> None:
    assert _run(tmp_path, "### DCO", summary=False) == ""
