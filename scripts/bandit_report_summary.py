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

``--sarif-out`` writes a SARIF 2.1.0 file for the GitHub Security tab. When
``GITHUB_STEP_SUMMARY`` is set, a short markdown table is appended there.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_SARIF_LEVEL = {"HIGH": "error", "MEDIUM": "warning", "LOW": "note"}


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


def _write_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def _write_sarif(data: dict[str, Any], dest: Path) -> None:
    raw_results = data.get("results", [])
    if not isinstance(raw_results, list):
        raw_results = []
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("test_id") or "unknown")
        try:
            line = int(item.get("line_number") or 1)
        except (TypeError, ValueError):
            line = 1
        if line < 1:
            line = 1
        filename = str(item.get("filename") or "unknown").replace("\\", "/").lstrip("/")
        text = str(item.get("issue_text") or rule_id).replace("\r", " ").strip()
        rules.setdefault(
            rule_id,
            {
                "id": rule_id,
                "shortDescription": {"text": rule_id},
                "helpUri": str(item.get("more_info") or "https://bandit.readthedocs.io/"),
            },
        )
        results.append(
            {
                "ruleId": rule_id,
                "level": _SARIF_LEVEL.get(str(item.get("issue_severity") or ""), "warning"),
                "message": {"text": text},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": filename},
                            "region": {"startLine": line},
                        }
                    }
                ],
            }
        )
    sarif = {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Bandit",
                        "informationUri": "https://bandit.readthedocs.io/",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    dest.write_text(json.dumps(sarif, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", default="bandit-report.json")
    parser.add_argument("--sarif-out", type=Path)
    args = parser.parse_args()

    path = Path(args.report)
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
    _write_summary(
        "\n".join(
            [
                "### Bandit",
                "",
                f"- Lines scanned (approx): {loc}",
                f"- High: {high}",
                f"- Medium: {medium}",
                f"- Low: {low}",
                "",
                "High severity fails the next step. The SARIF upload lists every finding.",
                "",
            ]
        )
    )
    if args.sarif_out is not None:
        _write_sarif(data, args.sarif_out)
        print(f"SARIF: {args.sarif_out}")


if __name__ == "__main__":
    main()
