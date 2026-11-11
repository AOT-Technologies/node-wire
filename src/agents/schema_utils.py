#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Shared JSON-Schema helpers for LLM provider tool definitions."""

from __future__ import annotations

import copy
import os
from typing import Any, Callable, Dict, List, Optional


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


def llm_request_timeout_seconds() -> float:
    """Per-request LLM timeout (``NW_LLM_TIMEOUT_SECONDS``, default 120 s).

    SDK defaults are 600 s (OpenAI, Anthropic) or unbounded (Gemini); one hung
    call would otherwise hold an agent step for ten minutes.
    """
    try:
        value = float(os.environ.get("NW_LLM_TIMEOUT_SECONDS", "120"))
    except ValueError:
        return 120.0
    return value if value > 0 else 120.0


class ToolConversionCache:
    """Convert an MCP tool list once per agent run instead of on every step.

    The agent passes the same list object to every ``chat_with_tools`` call; the
    length check also catches a list extended in place.
    """

    def __init__(self) -> None:
        self._src: Optional[List[Dict[str, Any]]] = None
        self._len = -1
        self._out: List[Any] = []

    def get(
        self,
        tools: List[Dict[str, Any]],
        convert: Callable[[Dict[str, Any]], Any],
    ) -> List[Any]:
        if tools is not self._src or len(tools) != self._len:
            self._src, self._len = tools, len(tools)
            self._out = [convert(t) for t in tools]
        return self._out
