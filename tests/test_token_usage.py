#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""TokenUsage normalisation, provider extraction, and serialisation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.llm_base import LLMMessage, TokenUsage
from agents.providers.anthropic_provider import _usage_from_response as anthropic_usage
from agents.providers.gemini_provider import _usage_from_response as gemini_usage
from agents.providers.groq_provider import _usage_from_response as groq_usage
from agents.providers.openai_provider import _usage_from_response as openai_usage
from agents.toolhive import _usage_as_dict


def test_token_usage_add_sums_and_treats_none_as_zero() -> None:
    a = TokenUsage(prompt_tokens=10, completion_tokens=None, total_tokens=10)
    b = TokenUsage(prompt_tokens=None, completion_tokens=5, total_tokens=5)
    out = a + b
    assert out.prompt_tokens == 10
    assert out.completion_tokens == 5
    assert out.total_tokens == 15


def test_token_usage_add_both_none_stays_none() -> None:
    a = TokenUsage()
    b = TokenUsage()
    out = a + b
    assert out.prompt_tokens is None
    assert out.completion_tokens is None
    assert out.total_tokens is None


def test_usage_as_dict_none_when_no_usage() -> None:
    assert _usage_as_dict(None, steps_used=3) is None


def test_usage_as_dict_includes_steps() -> None:
    usage = TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    assert _usage_as_dict(usage, steps_used=2) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "steps": 2,
    }


def test_openai_usage_from_response_present() -> None:
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    )
    assert openai_usage(resp) == TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)


def test_openai_usage_from_response_missing() -> None:
    assert openai_usage(SimpleNamespace(usage=None)) is None
    assert openai_usage(SimpleNamespace()) is None


def test_groq_usage_from_response_present() -> None:
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    )
    assert groq_usage(resp) == TokenUsage(prompt_tokens=3, completion_tokens=4, total_tokens=7)


def test_anthropic_usage_sums_input_output() -> None:
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=40, output_tokens=10))
    assert anthropic_usage(resp) == TokenUsage(
        prompt_tokens=40, completion_tokens=10, total_tokens=50
    )


def test_anthropic_usage_empty_block_is_none() -> None:
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=None, output_tokens=None))
    assert anthropic_usage(resp) is None
    assert anthropic_usage(SimpleNamespace(usage=None)) is None


def test_gemini_usage_from_metadata() -> None:
    resp = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=9,
            candidates_token_count=2,
            total_token_count=11,
        )
    )
    assert gemini_usage(resp) == TokenUsage(prompt_tokens=9, completion_tokens=2, total_tokens=11)
    assert gemini_usage(SimpleNamespace(usage_metadata=None)) is None


def _openai_style_response_with_usage(
    content: str, *, prompt: int, completion: int, total: int
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
    )
    return resp


def test_openai_provider_chat_with_tools_attaches_usage() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _openai_style_response_with_usage(
        "hi", prompt=5, completion=2, total=7
    )
    with patch("agents.providers.openai_provider.OpenAI", return_value=mock_client):
        from agents.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="k", model="m")
    out = p.chat_with_tools([LLMMessage(role="user", content="x")], [])
    assert out.usage is not None
    assert out.usage.prompt_tokens == 5
    assert out.usage.completion_tokens == 2
    assert out.usage.total_tokens == 7


def test_openai_provider_chat_with_tools_omits_usage_when_absent() -> None:
    msg = MagicMock()
    msg.content = "hi"
    msg.tool_calls = []
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = None
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    with patch("agents.providers.openai_provider.OpenAI", return_value=mock_client):
        from agents.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="k", model="m")
    out = p.chat_with_tools([LLMMessage(role="user", content="x")], [])
    assert out.usage is None
