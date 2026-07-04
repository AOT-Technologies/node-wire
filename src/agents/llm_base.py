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
class LLMResponse:
    """Raw response from the LLM."""

    content: Optional[str]
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "stop"  # "stop" | "tool_calls"

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
