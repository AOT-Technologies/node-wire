# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Remote ``$ref`` policy (SSRF hard-fail)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse


def _is_remote_ref(ref: str, *, from_url: bool) -> bool:
    if not isinstance(ref, str) or not ref:
        return False
    if ref.startswith("#"):
        return False
    parsed = urlparse(ref)
    if parsed.scheme in {"http", "https", "ftp", "file"}:
        return True
    # scheme-relative or other absolute URI
    if parsed.scheme and parsed.scheme not in {"", "about"}:
        return True
    # Relative file refs: allowed only when root was a local path
    if from_url:
        return True
    return False


def collect_remote_refs(obj: Any, *, from_url: bool, _acc: list[str] | None = None) -> list[str]:
    acc = _acc if _acc is not None else []
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and _is_remote_ref(ref, from_url=from_url):
            acc.append(ref)
        for v in obj.values():
            collect_remote_refs(v, from_url=from_url, _acc=acc)
    elif isinstance(obj, list):
        for item in obj:
            collect_remote_refs(item, from_url=from_url, _acc=acc)
    return acc


def assert_no_remote_refs(doc: dict[str, Any], *, from_url: bool) -> None:
    remotes = collect_remote_refs(doc, from_url=from_url)
    if remotes:
        raise ValueError(f"remote $ref rejected: {remotes}")


class _NotLocal(Exception):
    """The document needs prance (file refs, odd ``$ref`` shapes); not an error."""


# Refs under these prefixes are resolved once and shared between their occurrences.
_SHARED_PREFIXES = ("components/schemas/",)


def _local_refs(node: Any) -> list[str]:
    """Every ``$ref`` under ``node`` in document order (prance's ``reference_iterator``)."""
    found: list[str] = []
    stack = [node]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if len(value) != 1 or not isinstance(ref, str) or not ref.startswith("#"):
                    raise _NotLocal
                found.append(ref)
                continue
            stack.extend(reversed([v for v in value.values() if isinstance(v, (dict, list))]))
        elif isinstance(value, list):
            stack.extend(reversed([v for v in value if isinstance(v, (dict, list))]))
    return found


def _back_edges(roots: list[str], edges: dict[str, list[str]]) -> set[tuple[str, str]]:
    """The ``(target, dependency)`` edges prance's ``RefResolver`` cuts to break cycles.

    Same traversal as prance: targets in its discovery order (a stack seeded with the
    document's refs), then a depth-first search over them marking edges back into the
    current path. Cutting the same edges gives the same resolved document.
    """
    order: list[str] = []
    seen = set(roots)
    stack = list(dict.fromkeys(roots))
    while stack:
        ref = stack.pop()
        order.append(ref)
        for dep in edges[ref]:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)

    white, gray, black = 0, 1, 2
    color = dict.fromkeys(order, white)
    back: set[tuple[str, str]] = set()
    for start in order:
        if color[start] != white:
            continue
        path = [(start, 0)]
        while path:
            node, idx = path[-1]
            if idx == 0:
                color[node] = gray
            if idx < len(edges[node]):
                path[-1] = (node, idx + 1)
                dep = edges[node][idx]
                if color[dep] == white:
                    path.append((dep, 0))
                elif color[dep] == gray:
                    back.add((node, dep))
            else:
                color[node] = black
                path.pop()
    return back


def resolve_local_refs(
    doc: dict[str, Any], on_cycle: Callable[[str], Any] | None = None
) -> dict[str, Any] | None:
    """Inline every same-document ``#/…`` ``$ref``, like prance's ``RefResolver``, or None.

    Each ``#/components/schemas/…`` target is resolved once and the result shared (the
    output equals prance's by value; downstream code only reads schema nodes). Other
    targets (parameters, responses, path items) are resolved per occurrence into fresh
    containers, because derive annotates operation dicts in place.

    A ``$ref`` cycle is broken on the same edges prance breaks: each such reference is
    replaced by ``on_cycle(ref)``, or raises ``ValueError`` (prance's default) when
    ``on_cycle`` is None.

    Returns None when prance must handle the document: refs to other files, a ``$ref``
    with sibling keys or a non-string value, or a pointer through a list.
    """

    def target(ref: str) -> Any:
        node: Any = doc
        for part in ref[1:].split("/"):
            if not part:
                continue
            if not isinstance(node, dict):
                raise _NotLocal
            key = part.replace("~1", "/").replace("~0", "~")
            if key not in node:
                raise ValueError(f'Cannot resolve reference "{ref}"')
            node = node[key]
        return node

    try:
        roots = _local_refs(doc)
        edges: dict[str, list[str]] = {}
        pending = list(dict.fromkeys(roots))
        while pending:
            ref = pending.pop()
            if ref not in edges:
                edges[ref] = _local_refs(target(ref))
                pending.extend(d for d in edges[ref] if d not in edges)
    except _NotLocal:
        return None
    back = _back_edges(roots, edges)

    memo: dict[str, Any] = {}

    def deref(ref: str, parent: str | None) -> Any:
        if (parent, ref) in back:
            if on_cycle is None:
                raise ValueError(f'Recursion reached limit of 1 trying to resolve "{ref}"')
            return on_cycle(ref)
        if ref in memo:
            return memo[ref]
        value = walk(target(ref), ref)
        if ref.lstrip("#/").startswith(_SHARED_PREFIXES):
            memo[ref] = value
        return value

    def walk(node: Any, owner: str | None) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return deref(node["$ref"], owner)
            return {k: walk(v, owner) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, owner) for v in node]
        return node

    return walk(doc, None)
