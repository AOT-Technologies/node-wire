"""
OpenAI LLM Provider
===================
Uses the openai SDK with native function-calling.

Required env var:  OPENAI_API_KEY
Optional env var:  OPENAI_MODEL  (default: gpt-4o-mini)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from agents.llm_factory import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("agents.providers.openai")


def _mcp_tool_to_openai(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _messages_to_openai(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
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
            result.append(
                {
                    "role": "assistant",
                    "content": m.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            result.append({"role": m.role, "content": m.content or ""})
    return result


try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class OpenAIProvider(BaseLLMProvider):
    """OpenAI LLM provider with native tool calling."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if OpenAI is None:
            raise ImportError("openai SDK not installed. Run: pip install 'node-wire[agents]'")
        self._client = OpenAI(api_key=api_key)
        self._model = model
        logger.info("OpenAIProvider initialised | model=%s", model)

    def chat_with_tools(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
    ) -> LLMResponse:
        oai_messages = _messages_to_openai(messages)
        oai_tools = [_mcp_tool_to_openai(t) for t in tools] if tools else []

        kwargs: Dict[str, Any] = {"model": self._model, "messages": oai_messages}
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

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
