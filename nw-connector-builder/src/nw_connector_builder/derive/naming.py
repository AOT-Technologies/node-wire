# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Action naming with ≤40 + collision suffix (generator-owned)."""

from __future__ import annotations

import re


def to_snake_case(value: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    if not s:
        s = "action"
    if s[0].isdigit():
        s = "a_" + s
    return s


def fallback_operation_name(method: str, path: str) -> str:
    parts = [method.lower()]
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            parts.append(to_snake_case(seg[1:-1]))
        else:
            parts.append(to_snake_case(seg))
    return "_".join(parts) or f"{method.lower()}_root"


def normalize_action_name(raw: str) -> str:
    name = to_snake_case(raw)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        name = "a_" + re.sub(r"[^a-z0-9_]", "", name)
        if not name or name[0].isdigit():
            name = "action"
    return name


def uniquify_names(candidates: list[str], *, max_len: int = 40) -> list[str]:
    """Truncate to max_len and append numeric suffixes for collisions (document order)."""
    used: dict[str, int] = {}
    result: list[str] = []
    for raw in candidates:
        base = normalize_action_name(raw)[:max_len]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", base):
            base = (base + "x")[:max_len]
        name = base
        if name in used:
            n = used[name] + 1
            while True:
                suffix = f"_{n}"
                trimmed = base[: max_len - len(suffix)]
                candidate = trimmed + suffix
                if candidate not in used:
                    name = candidate
                    used[base] = n
                    used[name] = 0
                    break
                n += 1
        else:
            used[name] = 0
        result.append(name)
    return result
