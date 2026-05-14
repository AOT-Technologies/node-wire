#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Gemini LLM Provider
===================
Uses the google-generativeai SDK. Gemini uses a different function-calling
schema (FunctionDeclaration + Tool), so this provider translates between
MCP tool format and Gemini's format.

Required env var:  GEMINI_API_KEY
Optional env var:  GEMINI_MODEL  (default: gemini-2.0-flash)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from agents.llm_factory import BaseLLMProvider, LLMMessage, LLMResponse, ToolCall

logger = logging.getLogger("agents.providers.gemini")


def _mcp_schema_to_gemini(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a JSON Schema dict to Gemini-compatible parameter schema."""
    # Gemini uses a subset of JSON Schema — strip unsupported keys
    allowed = {"type", "description", "properties", "required", "items", "enum"}
    cleaned = {k: v for k, v in schema.items() if k in allowed}
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: _mcp_schema_to_gemini(v) for k, v in cleaned["properties"].items()
        }
    return cleaned


genai: Any = None
try:
    import google.generativeai as _genai

    genai = _genai
except ImportError:
    pass


class GeminiProvider(BaseLLMProvider):
    """Google Gemini LLM provider with function calling."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash") -> None:
        if genai is None:
            raise ImportError(
                "google-generativeai not installed. Run: pip install 'node-wire[agents]'"
            )
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = model
        self._history: List[Any] = []
        logger.info("GeminiProvider initialised | model=%s", model)

    def chat_with_tools(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
    ) -> LLMResponse:
        genai = self._genai
        from google.generativeai.types import FunctionDeclaration, Tool  # type: ignore

        # Build Gemini tools
        gemini_tools: Optional[List[Any]] = None
        if tools:
            decls = []
            for t in tools:
                schema = _mcp_schema_to_gemini(
                    t.get("input_schema", {"type": "object", "properties": {}})
                )
                decls.append(
                    FunctionDeclaration(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=schema,
                    )
                )
            gemini_tools = [Tool(function_declarations=decls)]

        # Translate conversation to Gemini Contents format
        chat_history = []
        system_prompt: Optional[str] = None
        last_user_content: Optional[str] = None

        for m in messages:
            if m.role == "system":
                system_prompt = m.content
                continue
            if m.role == "user":
                last_user_content = m.content
                chat_history.append({"role": "user", "parts": [m.content or ""]})
            elif m.role == "assistant":
                parts = []
                if m.content:
                    parts.append(m.content)
                if m.tool_calls:
                    for tc in m.tool_calls:
                        parts.append(
                            genai.protos.Part(
                                function_call=genai.protos.FunctionCall(
                                    name=tc.name, args=tc.arguments
                                )
                            )
                        )
                chat_history.append({"role": "model", "parts": parts})
            elif m.role == "tool":
                chat_history.append(
                    {
                        "role": "function",
                        "parts": [
                            genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=m.name or "tool",
                                    response={"result": m.content or ""},
                                )
                            )
                        ],
                    }
                )

        model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=system_prompt,
        )
        chat = model.start_chat(history=chat_history[:-1] if chat_history else [])
        last_msg = chat_history[-1]["parts"] if chat_history else [last_user_content or ""]
        response = chat.send_message(last_msg, tools=gemini_tools)

        # Parse response
        tool_calls: List[ToolCall] = []
        text_parts: List[str] = []
        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                fc = part.function_call
                tool_calls.append(
                    ToolCall(
                        id=str(uuid.uuid4()),
                        name=fc.name,
                        arguments=dict(fc.args),
                    )
                )
            elif hasattr(part, "text") and part.text:
                text_parts.append(part.text)

        return LLMResponse(
            content=" ".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            stop_reason="tool_calls" if tool_calls else "stop",
        )
