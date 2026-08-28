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
