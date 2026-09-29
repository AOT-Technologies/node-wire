# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Action naming: length budget, word-boundary cuts, collision suffix (generator-owned)."""

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


# MCP tool names are `<connector_id>_<action>`; clients cap them at 64 characters.
MCP_TOOL_NAME_LIMIT = 64
# Floor for the per-action budget, so a very long connector id still leaves
# readable action names (the tool name then exceeds the limit — see derive).
_MIN_ACTION_NAME_LEN = 16


def action_name_budget(connector_id: str) -> int:
    """Longest action name that keeps ``<connector_id>_<action>`` within the MCP limit."""
    return max(_MIN_ACTION_NAME_LEN, MCP_TOOL_NAME_LIMIT - len(connector_id) - 1)


def _truncate(name: str, max_len: int) -> str:
    """Cut to ``max_len`` at the last word boundary, never leaving a trailing ``_``.

    A mid-word cut (``…_restrict_access_remo``) reads as a different word; a
    single word longer than the budget is still cut hard.
    """
    if len(name) <= max_len:
        return name
    cut = name[:max_len]
    if name[max_len] != "_" and "_" in cut:
        cut = cut[: cut.rfind("_")]
    return cut.rstrip("_") or name[:max_len]


def uniquify_names(candidates: list[str], *, max_len: int = 40) -> list[str]:
    """Truncate to max_len and append numeric suffixes for collisions (document order)."""
    used: dict[str, int] = {}
    result: list[str] = []
    for raw in candidates:
        base = _truncate(normalize_action_name(raw), max_len)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", base):
            base = (base + "x")[:max_len]
        name = base
        if name in used:
            n = used[name] + 1
            while True:
                suffix = f"_{n}"
                trimmed = _truncate(base, max_len - len(suffix))
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
