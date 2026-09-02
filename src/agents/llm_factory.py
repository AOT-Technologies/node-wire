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
  ollama                — local OpenAI-compatible (e.g. qwen2.5:7b)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
from urllib.parse import urlparse, urlunparse


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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_API_KEY = "ollama"

NVIDIA_TOOLS_NOTE = (
    "Tool calling may be limited on this model. If tool calls fail, switch back to Groq."
)
OLLAMA_TOOLS_NOTE = (
    "Tool calling may be limited on local Ollama models. If tool calls fail, switch back to Groq."
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


def normalize_openai_compatible_base_url(base_url: str) -> str:
    """Normalize a playground OpenAI-compatible base URL (ensure ``/v1`` suffix)."""
    raw = (base_url or "").strip()
    if not raw:
        return DEFAULT_OLLAMA_BASE_URL
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme {parsed.scheme!r}; use http or https.")
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v1"):
        normalized_path = path
    elif path:
        normalized_path = f"{path}/v1"
    else:
        normalized_path = "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))


def ollama_origin_from_base_url(base_url: str) -> str:
    """Return Ollama server origin (no ``/v1``) for native ``/api/tags`` calls."""
    normalized = normalize_openai_compatible_base_url(base_url)
    parsed = urlparse(normalized)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3] or ""
    return urlunparse((parsed.scheme, parsed.netloc, path or "", "", "", ""))


def resolve_ollama_base_url(override: Optional[str] = None) -> str:
    """Resolve Ollama OpenAI-compatible base URL from override or env."""
    if (override or "").strip():
        return normalize_openai_compatible_base_url(override or "")
    env_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
    if env_url:
        return normalize_openai_compatible_base_url(env_url)
    return DEFAULT_OLLAMA_BASE_URL


def resolve_ollama_api_key() -> str:
    return os.environ.get("OLLAMA_API_KEY", DEFAULT_OLLAMA_API_KEY).strip() or DEFAULT_OLLAMA_API_KEY


class LLMProviderFactory:
    """
    Creates the right ``BaseLLMProvider`` from environment variables.

    Environment variables:
        LLM_PROVIDER    : groq | openai | gemini | anthropic | nvidia | ollama  (default: groq)
        GROQ_API_KEY / GROQ_MODEL
        OPENAI_API_KEY / OPENAI_MODEL
        GEMINI_API_KEY / GEMINI_MODEL
        ANTHROPIC_API_KEY / ANTHROPIC_MODEL
        NVIDIA_API_KEY / NVIDIA_BASE_URL / NVIDIA_MODEL
        OLLAMA_API_KEY / OLLAMA_BASE_URL / OLLAMA_MODEL
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
        elif provider in ("openai", "nvidia", "ollama"):
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
            supported = ["groq", "openai", "gemini", "anthropic", "nvidia", "ollama"]
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
        elif provider == "ollama":
            kwargs["api_key"] = resolve_ollama_api_key()
            kwargs["model"] = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
            kwargs["base_url"] = resolve_ollama_base_url(None)

        return cls.create(provider, **kwargs)

    @classmethod
    def create_from_option(
        cls,
        llm_option: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> BaseLLMProvider:
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
        if provider == "ollama":
            return cls.create(
                "ollama",
                api_key=resolve_ollama_api_key(),
                model=model,
                base_url=resolve_ollama_base_url(base_url),
            )
        raise ValueError(
            f"Unsupported llm_option provider {provider!r}. "
            "Playground switcher supports: groq, nvidia, ollama."
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
                    "source": "env",
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
                    "source": "env",
                }
            )

        ollama_base = os.environ.get("OLLAMA_BASE_URL", "").strip()
        ollama_model = os.environ.get("OLLAMA_MODEL", "").strip()
        if ollama_base or ollama_model:
            resolved_model = ollama_model or DEFAULT_OLLAMA_MODEL
            resolved_base = resolve_ollama_base_url(ollama_base or None)
            ollama_id = f"ollama/{resolved_model}"
            options.append(
                {
                    "id": ollama_id,
                    "label": ollama_id,
                    "provider": "ollama",
                    "model": resolved_model,
                    "base_url": resolved_base,
                    "tools_note": OLLAMA_TOOLS_NOTE,
                    "source": "env",
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
