#!/usr/bin/env python3
#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Bump lockstep package versions for a Node Wire release.

Updates root + packages/**/pyproject.toml (not nw-mcp-builder), connector
node-wire-runtime dependency floors, and scaffolds a CHANGELOG.md section/link
when missing.

Usage:
  ./scripts/bump-version.py 1.0.1
  ./scripts/bump-version.py 1.0.1 --dry-run
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
PROJECT_VERSION_RE = re.compile(
    r'^version\s*=\s*"[^"]*"',
    re.MULTILINE,
)
RUNTIME_DEP_RE = re.compile(
    r'("node-wire-runtime)>=[^"]*"',
)
CHANGELOG_HEADING_RE = re.compile(
    r"^## \[([^\]]+)\](?: - \d{4}-\d{2}-\d{2})?\s*$",
    re.MULTILINE,
)
RELEASE_URL = "https://github.com/AOT-Technologies/node-wire/releases/tag/v{version}"


def _pyproject_paths() -> list[Path]:
    return [ROOT / "pyproject.toml", *sorted((ROOT / "packages").glob("**/pyproject.toml"))]


def _set_project_version(text: str, version: str, path: Path) -> str:
    if not PROJECT_VERSION_RE.search(text):
        raise SystemExit(f"ERROR: no project version field in {path}")
    return PROJECT_VERSION_RE.sub(f'version = "{version}"', text, count=1)


def _set_runtime_dep(text: str, version: str) -> str:
    return RUNTIME_DEP_RE.sub(rf'\1>={version}"', text)


def _changelog_has_section(text: str, version: str) -> bool:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}\s*$",
        re.MULTILINE,
    )
    return bool(pattern.search(text))


def _changelog_has_link(text: str, version: str) -> bool:
    pattern = re.compile(
        rf"^\[{re.escape(version)}\]: .+/releases/tag/v{re.escape(version)}\s*$",
        re.MULTILINE,
    )
    return bool(pattern.search(text))


def _scaffold_changelog(text: str, version: str) -> str:
    today = dt.date.today().isoformat()
    stub = (
        f"## [{version}] - {today}\n"
        "\n"
        "### Added\n"
        "\n"
        "- \n"
        "\n"
        "### Changed\n"
        "\n"
        "- \n"
        "\n"
    )

    updated = text
    if not _changelog_has_section(updated, version):
        unreleased = re.search(r"^## \[Unreleased\]\s*$", updated, re.MULTILINE)
        if not unreleased:
            raise SystemExit("ERROR: CHANGELOG.md is missing an ## [Unreleased] section")
        insert_at = unreleased.end()
        # Skip a single trailing newline after the Unreleased heading so the
        # stub sits cleanly between Unreleased and the previous release.
        if insert_at < len(updated) and updated[insert_at] == "\n":
            insert_at += 1
        updated = updated[:insert_at] + "\n" + stub + updated[insert_at:]

    if not _changelog_has_link(updated, version):
        link_line = f"[{version}]: {RELEASE_URL.format(version=version)}\n"
        # Insert before the first existing version link, or append at EOF.
        first_link = re.search(r"^\[\d+\.\d+\.\d+\]: ", updated, re.MULTILINE)
        if first_link:
            updated = updated[: first_link.start()] + link_line + updated[first_link.start() :]
        else:
            if not updated.endswith("\n"):
                updated += "\n"
            updated += "\n" + link_line

    return updated


def _write(path: Path, content: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bump lockstep Node Wire package versions for release.",
    )
    parser.add_argument(
        "version",
        help="Semver version without leading v (for example, 1.0.1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing files",
    )
    args = parser.parse_args(argv)
    version = args.version.strip()
    if version.startswith("v"):
        print("ERROR: pass the version without a leading v", file=sys.stderr)
        return 1
    if not VERSION_RE.fullmatch(version):
        print(f"ERROR: {version!r} is not a MAJOR.MINOR.PATCH version", file=sys.stderr)
        return 1

    changed: list[str] = []

    for path in _pyproject_paths():
        original = path.read_text(encoding="utf-8")
        updated = _set_project_version(original, version, path)
        if "node-wire-runtime>=" in updated:
            updated = _set_runtime_dep(updated, version)
        if updated != original:
            rel = path.relative_to(ROOT).as_posix()
            changed.append(rel)
            print(f"{'Would update' if args.dry_run else 'Updated'}: {rel}")
            _write(path, updated, dry_run=args.dry_run)

    changelog_path = ROOT / "CHANGELOG.md"
    changelog = changelog_path.read_text(encoding="utf-8")
    needs_section = not _changelog_has_section(changelog, version)
    needs_link = not _changelog_has_link(changelog, version)
    if needs_section or needs_link:
        updated_changelog = _scaffold_changelog(changelog, version)
        changed.append("CHANGELOG.md")
        bits = []
        if needs_section:
            bits.append("section")
        if needs_link:
            bits.append("link")
        action = "Would scaffold" if args.dry_run else "Scaffolded"
        print(f"{action} CHANGELOG.md ({', '.join(bits)})")
        _write(changelog_path, updated_changelog, dry_run=args.dry_run)
    else:
        print(f"CHANGELOG.md already has [{version}] section and link")

    if not changed:
        print(f"Nothing to change; already at {version}")
    else:
        print(f"{'Dry-run complete' if args.dry_run else 'Done'}: {len(changed)} file(s) for {version}")
        if not args.dry_run:
            print("Fill in CHANGELOG.md notes before tagging and running Create Release Tag.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
