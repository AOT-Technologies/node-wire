#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for scripts/ci_step_summary.py."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_step_summary.py"
MATCHER = REPO_ROOT / ".github" / "matchers" / "mypy.json"


def _run(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], str]:
    summary = tmp_path / "summary.md"
    env = os.environ.copy()
    env["GITHUB_STEP_SUMMARY"] = str(summary)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    text = summary.read_text(encoding="utf-8") if summary.is_file() else ""
    return proc, text


def test_pip_audit_clean_summary(tmp_path: Path) -> None:
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": [], "fixes": []}), encoding="utf-8")
    proc, summary = _run(tmp_path, "pip-audit", str(report), "--title", "packages/runtime")
    assert proc.returncode == 0
    assert "No known vulnerabilities." in summary
    assert "packages/runtime" in summary
    assert "::error" not in proc.stdout


def test_pip_audit_vulnerability_is_annotated(tmp_path: Path) -> None:
    report = tmp_path / "audit.json"
    report.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "setuptools",
                        "version": "69.0.0",
                        "vulns": [
                            {
                                "id": "PYSEC-2026-1",
                                "fix_versions": ["83.0.0"],
                                "description": "example\nfinding",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    proc, summary = _run(tmp_path, "pip-audit", str(report))
    assert proc.returncode == 0
    assert "PYSEC-2026-1" in summary
    assert "::error title=PYSEC-2026-1::" in proc.stdout
    assert "example finding" in proc.stdout


def test_pytest_failure_annotation_and_coverage(tmp_path: Path) -> None:
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" ?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="1">
    <testcase classname="tests.test_mod" name="test_ok" file="tests/test_mod.py" line="4"/>
    <testcase classname="tests.test_mod" name="test_bad" file="tests/test_mod.py" line="9">
      <failure message="assert 1 == 2">trace</failure>
    </testcase>
    <testcase classname="tests.test_mod" name="test_skip" file="tests/test_mod.py" line="12">
      <skipped message="nope"/>
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.xml"
    coverage.write_text('<coverage line-rate="0.812" />', encoding="utf-8")
    proc, summary = _run(
        tmp_path, "pytest", str(junit), str(coverage), "--title", "Pytest (ubuntu)"
    )
    assert proc.returncode == 0
    assert "Tests: 3" in summary
    assert "Failures: 1" in summary
    assert "Skipped: 1" in summary
    assert "81.2%" in summary
    assert "::error title=pytest,file=tests/test_mod.py,line=9::" in proc.stdout


def test_mypy_log_counts(tmp_path: Path) -> None:
    log = tmp_path / "mypy.txt"
    log.write_text(
        "src/a.py:10:2: error: Bad [assignment]\n"
        "src/a.py:11:2: note: See above\n"
        "src/b.py:3:1: warning: Unused [misc]\n"
        "Found 1 error in 1 file (checked 2 source files)\n",
        encoding="utf-8",
    )
    proc, summary = _run(tmp_path, "mypy", str(log))
    assert proc.returncode == 0
    assert "1 error(s), 1 warning(s)" in summary


def test_sarif_counts_results(tmp_path: Path) -> None:
    report = tmp_path / "gitleaks.sarif"
    report.write_text(
        json.dumps({"runs": [{"results": [{}, {}]}, {"results": [{}]}]}),
        encoding="utf-8",
    )
    proc, summary = _run(tmp_path, "sarif", str(report), "--title", "Gitleaks")
    assert proc.returncode == 0
    assert "3 findings" in summary


def test_missing_report_exits_nonzero(tmp_path: Path) -> None:
    proc, _summary = _run(tmp_path, "pip-audit", str(tmp_path / "missing.json"))
    assert proc.returncode == 2
    assert "not found" in proc.stderr.lower()
    assert "::error::" in proc.stdout


def test_mypy_problem_matcher_matches_error_lines() -> None:
    matcher = json.loads(MATCHER.read_text(encoding="utf-8"))
    pattern = matcher["problemMatcher"][0]["pattern"][0]["regexp"]
    compiled = re.compile(pattern)
    hit = compiled.match("src/a.py:10:2: error: Bad [assignment]")
    assert hit is not None
    assert hit.group(1) == "src/a.py"
    assert hit.group(2) == "10"
    assert hit.group(4) == "error"
    assert compiled.match("Found 1 error in 1 file") is None
    assert compiled.match("src/a.py:11:2: note: See above") is None
