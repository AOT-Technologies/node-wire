#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Write a GitHub Actions step summary, and annotations where SARIF is not used.

The script exits 0 when the report parses, including when the report contains
failures. Callers keep the scanner's exit code so a missing report (exit 2)
is distinct from a failed scan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

_MYPY_LINE = re.compile(r"^.+?:\d+:\d+: (error|warning): .*$")
_ANNOTATION_CAP = 30


def _fail(message: str) -> None:
    print(f"::error::{_escape_message(message)}")
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _escape_message(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_message(text).replace(",", "%2C").replace(":", "%3A")


def _annotation(
    message: str, *, title: str, file: str | None = None, line: str | None = None
) -> None:
    props = [f"title={_escape_property(title)}"]
    if file:
        props.append(f"file={_escape_property(file)}")
    if line and line.isdigit() and int(line) > 0:
        props.append(f"line={line}")
    print(f"::error {','.join(props)}::{_escape_message(message)}")


def _summary(markdown: str) -> None:
    print(markdown, end="" if markdown.endswith("\n") else "\n")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def _load_json(path: Path) -> Any:
    if not path.is_file():
        _fail(f"report not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON at {path}: {exc}")
    raise AssertionError("unreachable")


def _pip_audit_deps(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        deps = data.get("dependencies", [])
    elif isinstance(data, list):
        deps = data
    else:
        _fail("pip-audit report root must be an object or a list")
    if not isinstance(deps, list):
        _fail("pip-audit report dependencies must be a list")
    return [dep for dep in deps if isinstance(dep, dict)]


def cmd_pip_audit(args: argparse.Namespace) -> None:
    deps = _pip_audit_deps(_load_json(Path(args.report)))
    rows: list[tuple[str, str, str, str]] = []
    for dep in deps:
        name = str(dep.get("name") or "?")
        version = str(dep.get("version") or "?")
        vulns = dep.get("vulns") or []
        if not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            vuln_id = str(vuln.get("id") or "vulnerability")
            fixes = vuln.get("fix_versions") or []
            fix = ", ".join(str(item) for item in fixes) if isinstance(fixes, list) else ""
            description = str(vuln.get("description") or "").replace("\n", " ").strip()
            rows.append((vuln_id, f"{name} {version}", fix or "none", description[:240]))

    title = str(args.title)
    lines = [f"### pip-audit ({title})", ""]
    if not rows:
        lines.append("No known vulnerabilities.")
    else:
        lines.extend(
            [
                f"{len(rows)} known vulnerability(ies).",
                "",
                "| ID | Package | Fix versions |",
                "| --- | --- | --- |",
            ]
        )
        lines.extend(
            f"| {vuln_id} | {package} | {fix} |"
            for vuln_id, package, fix, _ in rows[:_ANNOTATION_CAP]
        )
        if len(rows) > _ANNOTATION_CAP:
            lines.append("")
            lines.append(f"{len(rows) - _ANNOTATION_CAP} more are in the log.")
    lines.append("")
    _summary("\n".join(lines))
    for vuln_id, package, fix, description in rows[:_ANNOTATION_CAP]:
        detail = f"{package} fix in {fix}"
        if description:
            detail = f"{detail}. {description}"
        _annotation(detail, title=vuln_id)


def _suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    return [node for node in root.findall("testsuite") if isinstance(node, ET.Element)]


def cmd_pytest(args: argparse.Namespace) -> None:
    junit_path = Path(args.junit)
    if not junit_path.is_file():
        _fail(f"JUnit report not found: {junit_path}")
    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as exc:
        _fail(f"invalid JUnit XML at {junit_path}: {exc}")

    tests = failures = errors = skipped = 0
    failed_cases: list[tuple[str, str, str, str]] = []
    for suite in _suites(root):
        for case in suite.findall("testcase"):
            tests += 1
            failure = case.find("failure")
            error = case.find("error")
            if case.find("skipped") is not None:
                skipped += 1
            problem = failure if failure is not None else error
            if problem is None:
                continue
            if failure is not None:
                failures += 1
            else:
                errors += 1
            failed_cases.append(
                (
                    str(case.get("file") or ""),
                    str(case.get("line") or ""),
                    f"{case.get('classname', '')}::{case.get('name', '')}".strip(":"),
                    str(problem.get("message") or problem.text or "failed").replace("\n", " ")[
                        :300
                    ],
                )
            )

    coverage = "not recorded"
    if args.coverage:
        coverage_path = Path(args.coverage)
        if coverage_path.is_file():
            try:
                raw_rate = ET.parse(coverage_path).getroot().get("line-rate")
                rate = float(raw_rate) if raw_rate is not None else None
            except (ET.ParseError, ValueError):
                coverage = f"unreadable ({coverage_path.name})"
            else:
                if rate is None:
                    coverage = f"unreadable ({coverage_path.name})"
                else:
                    coverage = f"{rate * 100:.1f}%"
        else:
            coverage = "missing"

    title = str(args.title)
    lines = [
        f"### {title}",
        "",
        f"- Tests: {tests}",
        f"- Failures: {failures}",
        f"- Errors: {errors}",
        f"- Skipped: {skipped}",
        f"- Line coverage: {coverage}",
        "",
    ]
    _summary("\n".join(lines))
    for file, line, name, message in failed_cases[:_ANNOTATION_CAP]:
        _annotation(f"{name} — {message}", title="pytest", file=file or None, line=line or None)


def cmd_mypy(args: argparse.Namespace) -> None:
    path = Path(args.report)
    if not path.is_file():
        _fail(f"mypy log not found: {path}")
    errors = warnings = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"unreadable mypy log at {path}: {exc}")
    for line in lines:
        match = _MYPY_LINE.match(line)
        if match is None:
            continue
        if match.group(1) == "error":
            errors += 1
        else:
            warnings += 1
    if errors == 0 and warnings == 0:
        body = "Mypy: no issues."
    else:
        body = f"Mypy: {errors} error(s), {warnings} warning(s)."
    _summary(f"### Type check\n\n{body}\n")


def cmd_sarif(args: argparse.Namespace) -> None:
    data = _load_json(Path(args.report))
    count = 0
    if isinstance(data, dict):
        for run in data.get("runs") or []:
            if isinstance(run, dict) and isinstance(run.get("results"), list):
                count += len(run["results"])
    title = str(args.title)
    noun = "finding" if count == 1 else "findings"
    _summary(f"### {title}\n\n{count} {noun}. SARIF upload carries the file annotations.\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pip_audit = sub.add_parser("pip-audit")
    pip_audit.add_argument("report")
    pip_audit.add_argument("--title", default="environment")
    pip_audit.set_defaults(func=cmd_pip_audit)

    pytest = sub.add_parser("pytest")
    pytest.add_argument("junit")
    pytest.add_argument("coverage", nargs="?")
    pytest.add_argument("--title", default="Pytest")
    pytest.set_defaults(func=cmd_pytest)

    mypy = sub.add_parser("mypy")
    mypy.add_argument("report")
    mypy.set_defaults(func=cmd_mypy)

    sarif = sub.add_parser("sarif")
    sarif.add_argument("report")
    sarif.add_argument("--title", default="SARIF")
    sarif.set_defaults(func=cmd_sarif)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
