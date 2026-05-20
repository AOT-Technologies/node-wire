#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Anthropic (Claude) LLM Provider
================================
Uses the anthropic SDK. Claude uses a different tool-use format —
``tool_use`` content blocks instead of OpenAI-style ``tool_calls``.

Required env var:  ANTHROPIC_API_KEY
Optional env var:  ANTHROPIC_MODEL  (default: claude-3-5-haiku-20241022)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agents.llm_factory import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("agents.providers.anthropic")


def _mcp_tool_to_claude(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get("input_schema", {"type": "object", "properties": {}}),
    }


def _messages_to_claude(
    messages: List[LLMMessage],
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """Returns (claude_messages, system_prompt)."""
    system_prompt: Optional[str] = None
    result: List[Dict[str, Any]] = []

    for m in messages:
        if m.role == "system":
            system_prompt = m.content
            continue
        if m.role == "user":
            result.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            content: List[Any] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            result.append({"role": "assistant", "content": content})
        elif m.role == "tool":
            # Claude expects tool results as user messages with tool_result blocks
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content or "",
                        }
                    ],
                }
            )

    return result, system_prompt


anthropic: Any = None
try:
    import anthropic as _anthropic

    anthropic = _anthropic
except ImportError:
    pass


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude LLM provider with tool use."""

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-20241022") -> None:
        if anthropic is None:
            raise ImportError("anthropic SDK not installed. Run: pip install 'node-wire[agents]'")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        logger.info("AnthropicProvider initialised | model=%s", model)

    def chat_with_tools(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
    ) -> LLMResponse:
        claude_messages, system_prompt = _messages_to_claude(messages)
        claude_tools = [_mcp_tool_to_claude(t) for t in tools] if tools else []

        kwargs: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": claude_messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if claude_tools:
            kwargs["tools"] = claude_tools

        logger.debug(
            "Anthropic request | model=%s | messages=%d", self._model, len(claude_messages)
        )
        response = self._client.messages.create(**kwargs)

        tool_calls: List[ToolCall] = []
        text_parts: List[str] = []

        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )
            elif block.type == "text":
                text_parts.append(block.text)

        return LLMResponse(
            content=" ".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason="tool_calls" if tool_calls else "stop",
        )
