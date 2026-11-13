# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Remote ``$ref`` policy (SSRF hard-fail)."""

from __future__ import annotations

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


def resolve_local_refs(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Inline every same-document ``#/…`` ``$ref``, like prance's ``RefResolver``, or None.

    prance deep-copies and re-resolves a ref's target at every occurrence, so shared
    schemas cost their full transitive closure each time they are referenced. Here each
    ``#/components/schemas/…`` target is resolved once and the result shared (the output
    equals prance's by value; downstream code only reads schema nodes). Other targets
    (parameters, responses, path items) are still resolved per occurrence into fresh
    containers, because derive annotates operation dicts in place.

    Returns None when prance must handle the document: refs to other files, a ``$ref``
    with sibling keys or a non-string value, or a pointer through a list. A recursive
    ref raises ``ValueError``, as prance's default recursion limit does.
    """
    memo: dict[str, Any] = {}
    active: set[str] = set()

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

    def deref(ref: str) -> Any:
        if ref in memo:
            return memo[ref]
        if ref in active:
            raise ValueError(f'Recursion reached limit of 1 trying to resolve "{ref}"')
        active.add(ref)
        try:
            value = walk(target(ref))
        finally:
            active.discard(ref)
        if ref.lstrip("#/").startswith(_SHARED_PREFIXES):
            memo[ref] = value
        return value

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                if len(node) != 1 or not isinstance(ref, str) or not ref.startswith("#"):
                    raise _NotLocal
                return deref(ref)
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    try:
        return walk(doc)
    except _NotLocal:
        return None
