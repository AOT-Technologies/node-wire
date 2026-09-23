#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for .github/scripts/step_summary.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "step_summary.sh"


def _run(tmp_path: Path, *args: str, stdin: str | None = None, summary: bool = True) -> str:
    dest = tmp_path / "summary.md"
    env = os.environ.copy()
    if summary:
        env["GITHUB_STEP_SUMMARY"] = str(dest)
    else:
        env.pop("GITHUB_STEP_SUMMARY", None)
    subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        input=stdin,
        text=True,
        env=env,
        check=True,
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
