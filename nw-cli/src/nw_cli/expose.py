# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``nw expose``: author action exposure profiles (``expose/<connector_id>/<profile>.yaml``).

A profile holds ``select:`` / ``exclude:`` selectors, which only this CLI reads, and the exact
``actions:`` they resolve to, which is all the runtime enforces
(:mod:`node_wire_runtime.exposure`). ``resolve`` re-expands the selectors after a connector
changes; ``resolve --check`` fails when a committed list is out of date.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from nw_stacklok.connector import load_connector_class
from rich.prompt import Prompt

from nw_cli import ui
from nw_cli.prerequisites import is_interactive
from nw_cli.root import resolve_node_wire_root
from nw_cli.ui import console, guard
from node_wire_runtime.exposure import _connector_version
from node_wire_runtime.tool_search import Bm25Index

SEARCH_LIMIT = 20  # how many matches ``nw expose search`` shows by default
# No ``search:`` kind: a stored selector must resolve the same way every time, and a
# BM25 ranking can shift when the connector changes. Search is for finding names only.
_KINDS = ("name", "path", "method", "tag")
_METADATA_FIELD = {"path": "http_path", "method": "http_method", "tag": "tags"}

expose_app = typer.Typer(
    name="expose",
    help="Choose which connector actions a deployment exposes (NW_EXPOSE_PROFILE).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


class ExposeError(ValueError):
    """A selector or profile is wrong; the message says how (a user error, not a bug in nw)."""


def _metas(node_wire_root: Path, connector_id: str) -> tuple[type, dict[str, Any]]:
    cls = load_connector_class(node_wire_root, connector_id)
    return cls, cls.nw_action_metas()


def _search(metas: dict[str, Any], query: str, limit: int) -> list[str]:
    docs = {
        name: " ".join(
            [name, m.http_path or "", " ".join(m.tags), (m.input_model.__doc__ or "").strip()]
        )
        for name, m in metas.items()
    }
    return Bm25Index(docs).search(query, limit=limit)


@lru_cache(maxsize=256)
def _glob(pattern: str) -> re.Pattern[str]:
    """``*`` matches within one path segment, ``**`` across segments, ``?`` one character.

    A trailing ``/**`` also matches the path itself, so ``/branches/**`` covers ``/branches``.
    Every other character is literal (``{owner}`` and ``[`` included).
    """
    tail = ""
    if pattern.endswith("/**"):
        pattern, tail = pattern[:-3], "(?:/.*)?"
    out = []
    for part in re.split(r"(\*\*|\*|\?)", pattern):
        if part == "**":
            out.append(".*")
        elif part == "*":
            out.append("[^/]*")
        elif part == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(part))
    return re.compile("".join(out) + tail + r"\Z", re.DOTALL)


def match(metas: dict[str, Any], selector: str) -> set[str]:
    """Actions one selector picks: ``name:`` (default), ``path:``, ``method:``, ``tag:``.

    ``name:`` and ``path:`` take globs: ``*`` stays within one ``/`` segment, ``**`` crosses
    them, and a trailing ``/**`` also matches the path itself.
    """
    kind, sep, value = selector.partition(":")
    if not sep:
        kind, value = "name", selector
    kind, value = kind.strip().lower(), value.strip()
    if kind not in _KINDS or not value:
        raise ExposeError(
            f"Bad selector {selector!r}: use one of {', '.join(_KINDS)} as kind:value"
        )
    field = _METADATA_FIELD.get(kind)
    if field and not any(getattr(m, field, None) for m in metas.values()):
        raise ExposeError(
            f"Selector {selector!r} needs action {field}, which no action of this connector has "
            "(generated connectors carry it from the OpenAPI spec); use name: selectors"
        )
    if kind == "method":
        return {n for n, m in metas.items() if (m.http_method or "").upper() == value.upper()}
    if kind == "tag":
        return {n for n, m in metas.items() if value in m.tags}
    if kind == "path":
        return {n for n, m in metas.items() if _glob(value).match(m.http_path or "")}
    return {n for n in metas if _glob(value).match(n)}


def expand(metas: dict[str, Any], select: list[str], exclude: list[str]) -> list[str]:
    """The sorted action list: every ``select`` match minus every ``exclude`` match."""
    chosen: set[str] = set()
    for selector in select:
        found = match(metas, selector)
        if not found:
            raise ExposeError(f"Selector {selector!r} matches no action")
        chosen |= found
    for selector in exclude:
        found = match(metas, selector)
        if not found:
            # A mistyped exclude would silently expose what it was meant to hide.
            raise ExposeError(f"Exclude {selector!r} matches no action")
        chosen -= found
    return sorted(chosen)


def _profile_file(root: Path, connector_id: str, profile: str) -> Path:
    if not profile or "/" in profile or "\\" in profile or profile in (".", ".."):
        raise ExposeError(f"Profile must be a name, not a path: {profile!r}")
    return root / "expose" / connector_id / f"{profile}.yaml"


def _write(file: Path, doc: dict[str, Any]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True)
    file.write_text(
        "# Edit select/exclude, then run: nw expose resolve. The runtime reads only `actions`.\n"
        + body,
        encoding="utf-8",
        newline="\n",
    )


def _document(
    connector_id: str,
    profile: str,
    cls: type,
    select: list[str],
    exclude: list[str],
    actions: list[str],
    recorded_version: Optional[str] = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {"connector": connector_id}
    # Simplified: the version is advisory. When the connector is not installed here (CI
    # installs only the root project) keep the one the file already records, so an
    # unreadable version never makes a profile look stale.
    version = _connector_version(cls) or recorded_version
    if version:
        doc["connector_version"] = version
    doc["profile"] = profile
    doc["select"] = select
    if exclude:
        doc["exclude"] = exclude
    doc["actions"] = actions
    return doc


def _ask_selectors(metas: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Prompt loop: one selector per line, ``-`` in front excludes, a blank line finishes."""
    console.print(
        f"Selectors: {', '.join(_KINDS)} as kind:value (a bare word is an action name glob). "
        "Prefix with - to exclude, ? to search names by keywords. Blank line to finish.",
        highlight=False,
    )
    select: list[str] = []
    exclude: list[str] = []
    while True:
        line = Prompt.ask("selector", default="", console=console).strip()
        if not line:
            return select, exclude
        if line.startswith("?"):
            for name in _search(metas, line[1:], 10):
                m = metas[name]
                console.print(
                    f"  {name}  {m.http_method or '?'} {m.http_path or ''}", highlight=False
                )
            continue
        excluded = line.startswith("-")
        selector = line.lstrip("-").strip()
        try:
            found = sorted(match(metas, selector))
        except ExposeError as exc:
            console.print(str(exc), highlight=False)
            continue
        if not found:
            console.print(f"  {selector!r} matches no action; not added", highlight=False)
            continue
        (exclude if excluded else select).append(selector)
        preview = ", ".join(found[:5]) + (" ..." if len(found) > 5 else "")
        console.print(f"  {len(found)} actions: {preview}", highlight=False)
        try:
            console.print(f"  now exposed: {len(expand(metas, select, exclude))}", highlight=False)
        except ExposeError as exc:
            console.print(f"  {exc}", highlight=False)


@expose_app.command("new")
@guard
def new(
    connector_id: str = typer.Argument(..., help="Connector id, e.g. github"),
    profile: str = typer.Argument(..., help="Profile name, e.g. github-readonly"),
    select: Optional[list[str]] = typer.Option(
        None, "--select", "-s", help="Selector to include (repeatable), e.g. method:GET"
    ),
    exclude: Optional[list[str]] = typer.Option(
        None, "--exclude", "-x", help="Selector to remove (repeatable), e.g. path:/repos/*/hooks*"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing profile"),
) -> None:
    """Create expose/<connector_id>/<profile>.yaml from selectors (asked for on a terminal)."""
    root = resolve_node_wire_root()
    file = _profile_file(root, connector_id, profile)
    if file.exists() and not force:
        raise ExposeError(f"{file} exists; pass --force to overwrite")
    cls, metas = _metas(root, connector_id)
    chosen, removed = list(select or []), list(exclude or [])
    if not chosen:
        if not is_interactive():
            raise ui.usage_error("required without a terminal", flags="--select")
        chosen, removed = _ask_selectors(metas)
    actions = expand(metas, chosen, removed)
    if not actions:
        raise ExposeError("Nothing selected: the profile would expose no action")
    _write(file, _document(connector_id, profile, cls, chosen, removed, actions))
    console.print(
        f"Wrote {file.relative_to(root)}: {len(actions)} of {len(metas)} actions", highlight=False
    )


def _profiles(root: Path, connector_id: Optional[str], profile: Optional[str]) -> list[Path]:
    base = root / "expose"
    pattern = f"{connector_id or '*'}/{profile or '*'}.yaml"
    return sorted(base.glob(pattern)) if base.is_dir() else []


def _read_profile(file: Path) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExposeError(f"Cannot parse exposure profile {file}: {exc}") from exc
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise ExposeError(f"{file}: expected a mapping at the top level")
    return doc


def refresh_profiles(
    root: Path, connector_id: Optional[str] = None, profile: Optional[str] = None, *, write: bool
) -> list[Path]:
    """Re-expand profiles that have ``select:``; return those whose file differs (rewritten if ``write``).

    A hand-written profile (no ``select:``) is only checked for actions the connector lost.
    """
    stale: list[Path] = []
    for file in _profiles(root, connector_id, profile):
        cid = file.parent.name
        doc = _read_profile(file)
        cls, metas = _metas(root, cid)
        select, exclude = list(doc.get("select") or []), list(doc.get("exclude") or [])
        if not select:
            gone = [a for a in doc.get("actions") or [] if a not in metas]
            if gone:
                raise ExposeError(f"{file}: connector {cid} has no action(s) {', '.join(gone)}")
            continue
        fresh = _document(
            cid,
            file.stem,
            cls,
            select,
            exclude,
            expand(metas, select, exclude),
            doc.get("connector_version"),
        )
        if fresh == {k: doc.get(k) for k in fresh} and set(doc) == set(fresh):
            continue
        stale.append(file)
        if write:
            _write(file, fresh)
    return stale


@expose_app.command("resolve")
@guard
def resolve(
    connector_id: Optional[str] = typer.Argument(None, help="Only this connector"),
    profile: Optional[str] = typer.Argument(None, help="Only this profile"),
    check: bool = typer.Option(
        False, "--check", help="Change nothing; exit 1 if any actions list or version is stale"
    ),
) -> None:
    """Re-expand each profile's selectors into its exact actions list."""
    root = resolve_node_wire_root()
    if not _profiles(root, connector_id, profile):
        if check and not (connector_id or profile):
            console.print("No profiles to check", highlight=False)
            return
        raise ExposeError("No profiles found under expose/")
    stale = refresh_profiles(root, connector_id, profile, write=not check)
    for file in stale:
        console.print(
            f"{'stale' if check else 'updated'}: {file.relative_to(root)}", highlight=False
        )
    if check and stale:
        console.print("Run: nw expose resolve", highlight=False)
        raise typer.Exit(1)
    if not stale:
        console.print("Profiles up to date", highlight=False)


@expose_app.command("search")
@guard
def search(
    connector_id: str = typer.Argument(..., help="Connector id"),
    query: str = typer.Argument(..., help="Keywords, e.g. 'list branches'"),
    limit: int = typer.Option(SEARCH_LIMIT, "--limit", "-n", help="How many matches to show"),
) -> None:
    """Rank a connector's actions against keywords, to find names for selectors."""
    _, metas = _metas(resolve_node_wire_root(), connector_id)
    for name in _search(metas, query, limit):
        m = metas[name]
        console.print(f"{name}  {m.http_method or '?'} {m.http_path or ''}", highlight=False)
