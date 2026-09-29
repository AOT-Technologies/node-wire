#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Documentation lifecycle checks (docs/adr/0002-documentation-lifecycle.md).

Living pages describe current behaviour and carry no status tracker; ADRs are frozen
records that live only under the Decisions nav section; hand-kept lists match the code.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

import node_wire_runtime

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
ADR_DIR = DOCS / "adr"
ADR_RECORD = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")
STATUS_LINE = re.compile(r"^\s*(?:[-*]\s+)?\**Status:", re.MULTILINE)
DECISIONS_SECTION = "Decisions"


class _IgnoreTagsLoader(yaml.SafeLoader):
    """mkdocs.yml uses ``!!python/name`` tags; the nav does not need them resolved."""


_IgnoreTagsLoader.add_multi_constructor("tag:yaml.org,2002:python/", lambda *_: None)


def _nav_entries(node: object, section: str | None = None) -> list[tuple[str | None, str]]:
    """Flatten the nav into ``(top-level section, page path)`` pairs."""
    out: list[tuple[str | None, str]] = []
    if isinstance(node, list):
        for item in node:
            out += _nav_entries(item, section)
    elif isinstance(node, dict):
        for title, value in node.items():
            out += _nav_entries(value, section if section is not None else title)
    elif isinstance(node, str) and not node.startswith("http"):
        out.append((section, node))
    return out


def _nav() -> list[tuple[str | None, str]]:
    config = yaml.load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"), _IgnoreTagsLoader)
    return _nav_entries(config["nav"])


def _adr_records() -> list[Path]:
    return sorted(p for p in ADR_DIR.iterdir() if ADR_RECORD.match(p.name))


def test_living_pages_carry_no_status_line() -> None:
    offenders = [
        str(page.relative_to(DOCS))
        for page in DOCS.rglob("*.md")
        if ADR_DIR not in page.parents and STATUS_LINE.search(page.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        f"living pages must not carry a Status: line (move it to an ADR): {offenders}"
    )


@pytest.mark.parametrize("record", _adr_records(), ids=lambda p: p.name)
def test_adr_declares_status_and_date(record: Path) -> None:
    text = record.read_text(encoding="utf-8")
    assert re.search(r"\*\*Status:\*\* (Accepted|Superseded)", text), record.name
    assert re.search(r"\*\*Date:\*\* \d{4}-\d{2}-\d{2}", text), record.name


def test_every_adr_is_indexed() -> None:
    index = (ADR_DIR / "index.md").read_text(encoding="utf-8")
    missing = [p.name for p in _adr_records() if f"]({p.name})" not in index]
    assert not missing, f"add these ADRs to docs/adr/index.md: {missing}"


def test_adrs_sit_only_under_decisions_nav() -> None:
    misplaced = [
        (section, page)
        for section, page in _nav()
        if (page.startswith("adr/")) != (section == DECISIONS_SECTION)
    ]
    assert not misplaced, (
        f"ADRs belong only under '{DECISIONS_SECTION}', and nothing else there: {misplaced}"
    )


def test_redirect_stubs_are_out_of_nav() -> None:
    nav_pages = {page for _, page in _nav()}
    stubs = [
        str(page.relative_to(DOCS))
        for page in DOCS.rglob("*.md")
        if re.search(r"^# Moved:", page.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert stubs, "expected at least one redirect stub"
    assert not nav_pages.intersection(stubs), "redirect stubs must stay out of the nav"


def _public_api_names() -> set[str]:
    """Names listed under the ``node_wire_runtime`` section of docs/public-api.md.

    Only the backticked names before a bullet's `` — `` description count, so prose
    mentions of fields (``envelope_ok_field``) are not mistaken for exports.
    """
    text = (DOCS / "public-api.md").read_text(encoding="utf-8")
    section = text.split("## `node_wire_runtime`", 1)[1].split("\n## ", 1)[0]
    bullets = re.split(r"\n(?=- )", section)
    names: set[str] = set()
    for bullet in bullets:
        if not bullet.startswith("- "):
            continue
        head = bullet.split(" — ", 1)[0]
        for token in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`", head):
            if not token.startswith("__"):
                names.add(token)
    return names


def test_public_api_page_matches_runtime_all() -> None:
    documented = _public_api_names()
    exported = set(node_wire_runtime.__all__)
    assert documented - exported == set(), "documented in public-api.md but not exported"
    assert exported - documented == set(), (
        "exported from node_wire_runtime but missing from public-api.md"
    )


def test_packaging_inventory_matches_build_script() -> None:
    script = (REPO_ROOT / "scripts" / "build-packages.sh").read_text(encoding="utf-8")
    block = re.search(r"ALL_PACKAGES=\(\n(.*?)\n\)", script, re.DOTALL)
    assert block, "ALL_PACKAGES array not found in scripts/build-packages.sh"
    paths = [line.strip() for line in block.group(1).splitlines() if line.strip()]
    names = {
        tomllib.loads((REPO_ROOT / path / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "name"
        ]
        for path in paths
    }
    packaging = (DOCS / "packaging.md").read_text(encoding="utf-8")
    inventory = packaging.split("## Package inventory", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `([a-z0-9-]+)`", inventory, re.MULTILINE))
    assert listed == names, f"packaging.md inventory drifted from ALL_PACKAGES: {listed ^ names}"
