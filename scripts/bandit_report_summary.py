#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Print a concise Bandit JSON report summary for CI logs (always exits 0).

Bandit exits with a non-zero status when *any* severity finding exists, even if
the separate CI gate only enforces `--severity-level high`. Use `--exit-zero`
when generating JSON, then run this script to surface counts and a short list
without failing the job.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        print(f"ERROR: Bandit report not found: {path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"ERROR: Invalid Bandit JSON at {path}: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("ERROR: Bandit report root must be a JSON object", file=sys.stderr)
        sys.exit(2)
    return data


def main() -> None:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "bandit-report.json")
    data = _load_report(path)

    totals = data.get("metrics", {}).get("_totals", {})
    if not isinstance(totals, dict):
        totals = {}

    def _int(key: str) -> int:
        v = totals.get(key, 0)
        return int(v) if isinstance(v, (int, float)) else 0

    high = _int("SEVERITY.HIGH")
    medium = _int("SEVERITY.MEDIUM")
    low = _int("SEVERITY.LOW")
    loc = _int("loc")

    results = data.get("results", [])
    if not isinstance(results, list):
        results = []

    print("=== Bandit report summary ===")
    print(f"Report: {path.resolve()}")
    print(f"Lines scanned (approx): {loc}")
    print(f"Findings by severity — HIGH: {high}, MEDIUM: {medium}, LOW: {low}")
    print(f"Total result entries: {len(results)}")
    print()
    if results:
        print("Findings (file:line [severity] test_id — short text):")
        for r in results[:50]:
            if not isinstance(r, dict):
                continue
            fn = r.get("filename", "?")
            ln = r.get("line_number", "?")
            sev = r.get("issue_severity", "?")
            tid = r.get("test_id", "?")
            text = str(r.get("issue_text", "")).replace("\n", " ")[:120]
            print(f"  {fn}:{ln} [{sev}] {tid} — {text}")
        if len(results) > 50:
            print(f"  ... and {len(results) - 50} more (see full JSON artifact)")
    else:
        print("No findings in results[] (clean scan).")
    print()
    print(
        "CI gate: the following step enforces high severity only "
        "(`bandit ... --severity-level high`)."
    )


if __name__ == "__main__":
    main()
