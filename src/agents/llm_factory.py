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
  groq        (default) — llama-3.3-70b-versatile
  openai                — gpt-4o-mini
  gemini                — gemini-2.0-flash
  anthropic             — claude-3-5-haiku-20241022
  nvidia                — nvidia/nemotron-3.5-lightning-30b-a3b (OpenAI-compatible)
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

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

NVIDIA_TOOLS_NOTE = (
    "Tool calling may be limited on this model. If tool calls fail, switch back to Groq."
)

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


def parse_llm_option(llm_option: str) -> tuple[str, str]:
    """Split ``provider/model`` where model may itself contain slashes."""
    raw = (llm_option or "").strip()
    if not raw or "/" not in raw:
        raise ValueError(
            f"Invalid llm_option {llm_option!r}. Expected 'provider/model' "
            "(e.g. 'groq/openai/gpt-oss-120b')."
        )
    provider, model = raw.split("/", 1)
    provider = provider.lower().strip()
    model = model.strip()
    if not provider or not model:
        raise ValueError(
            f"Invalid llm_option {llm_option!r}. Expected 'provider/model'."
        )
    return provider, model


def looks_like_tool_calling_unsupported(error: str) -> bool:
    """Heuristic for provider errors that indicate missing function/tool calling."""
    text = (error or "").lower()
    needles = (
        "function calling",
        "function_call",
        "tools are not supported",
        "tool_choice",
        "does not support tools",
        "tool use is not supported",
        "tools parameter",
        "unsupported tool",
    )
    return any(n in text for n in needles)


class LLMProviderFactory:
    """
    Creates the right ``BaseLLMProvider`` from environment variables.

    Environment variables:
        LLM_PROVIDER    : groq | openai | gemini | anthropic | nvidia  (default: groq)
        GROQ_API_KEY / GROQ_MODEL
        OPENAI_API_KEY / OPENAI_MODEL
        GEMINI_API_KEY / GEMINI_MODEL
        ANTHROPIC_API_KEY / ANTHROPIC_MODEL
        NVIDIA_API_KEY / NVIDIA_BASE_URL / NVIDIA_MODEL
    """

    @classmethod
    def create(cls, provider: str, **kwargs: Any) -> BaseLLMProvider:
        """
        Instantiate a provider by name.

        Extra ``kwargs`` are forwarded to the provider constructor,
        e.g. ``api_key``, ``model``, ``base_url``.
        """
        provider = provider.lower().strip()

        if provider == "groq":
            if GroqProvider is None:
                raise ImportError("GroqProvider could not be loaded. Check dependencies.")
            return GroqProvider(**kwargs)
        elif provider in ("openai", "nvidia"):
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
            supported = ["groq", "openai", "gemini", "anthropic", "nvidia"]
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
            kwargs["model"] = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        elif provider == "openai":
            kwargs["api_key"] = os.environ.get("OPENAI_API_KEY", "")
            kwargs["model"] = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
            if base_url:
                kwargs["base_url"] = base_url
        elif provider == "gemini":
            kwargs["api_key"] = os.environ.get("GEMINI_API_KEY", "")
            kwargs["model"] = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        elif provider == "anthropic":
            kwargs["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
            kwargs["model"] = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
        elif provider == "nvidia":
            kwargs["api_key"] = os.environ.get("NVIDIA_API_KEY", "")
            kwargs["model"] = os.environ.get("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL)
            kwargs["base_url"] = os.environ.get(
                "NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL
            ).strip() or DEFAULT_NVIDIA_BASE_URL

        return cls.create(provider, **kwargs)

    @classmethod
    def create_from_option(cls, llm_option: Optional[str] = None) -> BaseLLMProvider:
        """
        Create a provider from a playground ``provider/model`` option id.

        When ``llm_option`` is empty, always use Groq from env (playground default),
        not the process ``LLM_PROVIDER`` value.
        """
        if not (llm_option or "").strip():
            return cls.create(
                "groq",
                api_key=os.environ.get("GROQ_API_KEY", ""),
                model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
            )

        provider, model = parse_llm_option(llm_option or "")
        if provider == "groq":
            return cls.create(
                "groq",
                api_key=os.environ.get("GROQ_API_KEY", ""),
                model=model,
            )
        if provider == "nvidia":
            return cls.create(
                "nvidia",
                api_key=os.environ.get("NVIDIA_API_KEY", ""),
                model=model,
                base_url=os.environ.get(
                    "NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL
                ).strip()
                or DEFAULT_NVIDIA_BASE_URL,
            )
        raise ValueError(
            f"Unsupported llm_option provider {provider!r}. "
            "Playground switcher supports: groq, nvidia."
        )

    @classmethod
    def list_playground_options(cls) -> Dict[str, Any]:
        """Return configured playground LLM options (keys present in env only)."""
        options: List[Dict[str, Any]] = []

        groq_key = os.environ.get("GROQ_API_KEY", "").strip()
        if groq_key:
            groq_model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
            groq_id = f"groq/{groq_model}"
            options.append(
                {
                    "id": groq_id,
                    "label": groq_id,
                    "provider": "groq",
                    "model": groq_model,
                    "tools_note": None,
                }
            )

        nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if nvidia_key:
            nvidia_model = (
                os.environ.get("NVIDIA_MODEL", DEFAULT_NVIDIA_MODEL).strip()
                or DEFAULT_NVIDIA_MODEL
            )
            nvidia_id = f"nvidia/{nvidia_model}"
            options.append(
                {
                    "id": nvidia_id,
                    "label": nvidia_id,
                    "provider": "nvidia",
                    "model": nvidia_model,
                    "tools_note": NVIDIA_TOOLS_NOTE,
                }
            )

        default_id = None
        for opt in options:
            if opt["provider"] == "groq":
                default_id = opt["id"]
                break
        if default_id is None and options:
            default_id = options[0]["id"]

        return {"options": options, "default_id": default_id}
