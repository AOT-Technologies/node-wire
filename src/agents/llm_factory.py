#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
LLM Provider Factory
====================
Pluggable LLM backend for the ToolHive agent.

Usage::

    from agents.llm_factory import LLMProviderFactory

    provider = LLMProviderFactory.create_from_env()
    response = provider.chat_with_tools(messages, tools)

Supported providers (set via LLM_PROVIDER env var):
  groq        (default) — llama3-8b-8192
  openai                — gpt-4o-mini
  gemini                — gemini-2.0-flash
  anthropic             — claude-3-5-haiku-20241022
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Type

# Re-exported for backward compatibility; the canonical home is agents.llm_base.
from agents.llm_base import (  # noqa: F401
    BaseLLMProvider,
    LLMMessage,
    LLMResponse,
    ToolCall,
)

__all__ = [
    "BaseLLMProvider",
    "LLMMessage",
    "LLMProviderFactory",
    "LLMResponse",
    "ToolCall",
]

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Optional provider classes when [agents] extras are not installed.
GroqProvider: Optional[Type[BaseLLMProvider]] = None
OpenAIProvider: Optional[Type[BaseLLMProvider]] = None
GeminiProvider: Optional[Type[BaseLLMProvider]] = None
AnthropicProvider: Optional[Type[BaseLLMProvider]] = None

try:
    from agents.providers.groq_provider import GroqProvider as _GroqProvider
    from agents.providers.openai_provider import OpenAIProvider as _OpenAIProvider
    from agents.providers.gemini_provider import GeminiProvider as _GeminiProvider
    from agents.providers.anthropic_provider import AnthropicProvider as _AnthropicProvider

    GroqProvider = _GroqProvider
    OpenAIProvider = _OpenAIProvider
    GeminiProvider = _GeminiProvider
    AnthropicProvider = _AnthropicProvider
except ImportError:
    # Leave all four as None; create() raises ImportError with a clear message.
    pass


class LLMProviderFactory:
    """
    Creates the right ``BaseLLMProvider`` from environment variables.

    Environment variables:
        LLM_PROVIDER    : groq | openai | gemini | anthropic  (default: groq)
        GROQ_API_KEY / GROQ_MODEL
        OPENAI_API_KEY / OPENAI_MODEL
        GEMINI_API_KEY / GEMINI_MODEL
        ANTHROPIC_API_KEY / ANTHROPIC_MODEL
    """

    @classmethod
    def create(cls, provider: str, **kwargs: Any) -> BaseLLMProvider:
        """
        Instantiate a provider by name.

        Extra ``kwargs`` are forwarded to the provider constructor,
        e.g. ``api_key``, ``model``.
        """
        provider = provider.lower().strip()

        if provider == "groq":
            if GroqProvider is None:
                raise ImportError("GroqProvider could not be loaded. Check dependencies.")
            return GroqProvider(**kwargs)
        elif provider == "openai":
            if OpenAIProvider is None:
                raise ImportError("OpenAIProvider could not be loaded. Check dependencies.")
            return OpenAIProvider(**kwargs)
        elif provider == "gemini":
            if GeminiProvider is None:
                raise ImportError("GeminiProvider could not be loaded. Check dependencies.")
            return GeminiProvider(**kwargs)
        elif provider == "anthropic":
            if AnthropicProvider is None:
                raise ImportError("AnthropicProvider could not be loaded. Check dependencies.")
            return AnthropicProvider(**kwargs)
        else:
            supported = ["groq", "openai", "gemini", "anthropic"]
            raise ValueError(
                f"Unknown LLM provider {provider!r}. Supported: {', '.join(supported)}"
            )

    @classmethod
    def create_from_env(cls) -> BaseLLMProvider:
        """Create a provider using LLM_PROVIDER and matching env vars."""
        provider = os.environ.get("LLM_PROVIDER", "groq").lower()
        kwargs: Dict[str, Any] = {}

        if provider == "groq":
            kwargs["api_key"] = os.environ.get("GROQ_API_KEY", "")
            kwargs["model"] = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        elif provider == "openai":
            kwargs["api_key"] = os.environ.get("OPENAI_API_KEY", "")
            kwargs["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        elif provider == "gemini":
            kwargs["api_key"] = os.environ.get("GEMINI_API_KEY", "")
            kwargs["model"] = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        elif provider == "anthropic":
            kwargs["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
            kwargs["model"] = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

        return cls.create(provider, **kwargs)
