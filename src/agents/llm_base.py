#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
LLM Provider Base
=================
Provider-agnostic data models and the abstract provider interface.

Kept separate from :mod:`agents.llm_factory` so provider implementations can
depend on the interface without importing the factory (which imports the
providers), avoiding a module-level import cycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data models (provider-agnostic)
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A single tool-call request returned by the LLM."""

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMMessage:
    """A single message in the conversation thread."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # required for role="tool" responses
    name: Optional[str] = None  # tool name for role="tool"


@dataclass
class TokenUsage:
    """Token counts for a single LLM call, normalised across providers.

    Fields are optional because not every backend reports usage (notably
    Ollama's OpenAI-compatible endpoint), and ``None`` must stay
    distinguishable from a genuine zero.
    """

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        def _add(a: Optional[int], b: Optional[int]) -> Optional[int]:
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        return TokenUsage(
            prompt_tokens=_add(self.prompt_tokens, other.prompt_tokens),
            completion_tokens=_add(self.completion_tokens, other.completion_tokens),
            total_tokens=_add(self.total_tokens, other.total_tokens),
        )


@dataclass
class LLMResponse:
    """Raw response from the LLM."""

    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "stop"  # "stop" | "tool_calls"
    usage: Optional[TokenUsage] = None

    @property
    def wants_tool_call(self) -> bool:
        return bool(self.tool_calls)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseLLMProvider(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    def chat_with_tools(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]],
    ) -> LLMResponse:
        """
        Send a conversation to the LLM, optionally with a set of tools.

        Parameters
        ----------
        messages:
            Full conversation history in provider-agnostic format.
        tools:
            List of MCP-style tool objects with ``name``, ``description``,
            and ``input_schema`` keys.

        Returns
        -------
        LLMResponse
            The model's response, which may include tool_calls.
        """
