#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for scripts/bandit_report_summary.py (CI log helper)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bandit_report_summary.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "bandit_minimal_report.json"


def test_bandit_report_summary_runs_on_fixture() -> None:
    assert SCRIPT.is_file(), "summary script must exist"
    assert FIXTURE.is_file(), "fixture must exist"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURE)],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "Bandit report summary" in proc.stdout
    assert "src/example.py" in proc.stdout
    assert "B999" in proc.stdout
    assert "HIGH: 0" in proc.stdout


def test_bandit_report_summary_missing_file_exits_nonzero() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(REPO_ROOT / "nonexistent_bandit.json")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "not found" in proc.stderr.lower()


def test_bandit_report_summary_invalid_json_exits_nonzero(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(bad)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_bandit_fixture_is_valid_json() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "metrics" in data
    assert "_totals" in data["metrics"]
    assert isinstance(data.get("results"), list)
