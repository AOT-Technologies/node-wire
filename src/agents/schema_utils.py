#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Shared JSON-Schema helpers for LLM provider tool definitions."""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional


def openai_compatible_tool_parameters(input_schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Copy a JSON Schema so optional properties also accept ``null``.

    Providers such as Groq validate model tool calls against the schema and reject
    ``"field": null`` when the type is only ``"string"``. Models often emit null
    for unused optional keys instead of omitting them.
    """
    schema = copy.deepcopy(input_schema) if input_schema else {"type": "object", "properties": {}}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema
    required = set(schema.get("required") or [])
    for key, prop in props.items():
        if key in required or not isinstance(prop, dict):
            continue
        t = prop.get("type")
        if t is None:
            continue
        if isinstance(t, list):
            if "null" not in t:
                prop["type"] = [*t, "null"]
        elif t != "null":
            prop["type"] = [t, "null"]
    return schema
