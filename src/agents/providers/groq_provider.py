#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Groq LLM Provider
=================
Uses the Groq SDK. Groq is OpenAI API-compatible, so tool-calling
uses the same schema and response format as OpenAI.

Required env var:  GROQ_API_KEY
Optional env var:  GROQ_MODEL  (default: llama-3.3-70b-versatile)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, cast

from agents.llm_factory import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("agents.providers.groq")


def _mcp_tool_to_groq(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an MCP tool descriptor to Groq's function schema."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _messages_to_groq(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
    result = []
    for m in messages:
        if m.role == "tool":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content or "",
                }
            )
        elif m.tool_calls:
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": m.content if m.content is not None else "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }
            result.append(cast(Dict[str, Any], assistant_msg))
        else:
            result.append({"role": m.role, "content": m.content or ""})
    return result


Groq: Any = None
try:
    from groq import Groq as _Groq

    Groq = _Groq
except ImportError:
    pass


class GroqProvider(BaseLLMProvider):
    """Groq-hosted LLM provider (OpenAI-compatible tool calling)."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        if Groq is None:
            raise ImportError("groq SDK not installed. Run: pip install 'node-wire[agents]'")
        self._client = Groq(api_key=api_key)
        self._model = model
        logger.info("GroqProvider initialised | model=%s", model)

    def chat_with_tools(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
    ) -> LLMResponse:
        groq_messages = _messages_to_groq(messages)
        groq_tools = [_mcp_tool_to_groq(t) for t in tools] if tools else []

        kwargs: Dict[str, Any] = {"model": self._model, "messages": groq_messages}
        if groq_tools:
            kwargs["tools"] = groq_tools
            kwargs["tool_choice"] = "auto"

        logger.debug(
            "Groq request | model=%s | messages=%d | tools=%d",
            self._model,
            len(groq_messages),
            len(groq_tools),
        )

        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls: List[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        stop_reason = "tool_calls" if tool_calls else "stop"
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )
