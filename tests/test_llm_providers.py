"""LLM factory and provider unit tests (SDKs mocked)."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from agents.llm_factory import LLMMessage, LLMProviderFactory, ToolCall


def test_llm_factory_create_gemini_and_anthropic() -> None:
    with patch("agents.providers.gemini_provider.genai") as genai_mod:
        genai_mod.configure = MagicMock()
        genai_mod.GenerativeModel = MagicMock()
        p = LLMProviderFactory.create("gemini", api_key="k", model="gemini-2.0-flash")
    from agents.providers.gemini_provider import GeminiProvider

    assert isinstance(p, GeminiProvider)

    with patch("agents.providers.anthropic_provider.anthropic") as anth_mod:
        anth_mod.Anthropic = MagicMock()
        p2 = LLMProviderFactory.create("anthropic", api_key="k", model="claude-3-5-haiku-20241022")
    from agents.providers.anthropic_provider import AnthropicProvider

    assert isinstance(p2, AnthropicProvider)


@pytest.mark.parametrize(
    ("env_provider", "env_key", "env_val", "model_env", "model_val", "expected_cls_path"),
    [
        (
            "groq",
            "GROQ_API_KEY",
            "gk",
            "GROQ_MODEL",
            "llama-x",
            "agents.providers.groq_provider.GroqProvider",
        ),
        (
            "openai",
            "OPENAI_API_KEY",
            "ok",
            "OPENAI_MODEL",
            "gpt-x",
            "agents.providers.openai_provider.OpenAIProvider",
        ),
        (
            "gemini",
            "GEMINI_API_KEY",
            "gk2",
            "GEMINI_MODEL",
            "gem-x",
            "agents.providers.gemini_provider.GeminiProvider",
        ),
        (
            "anthropic",
            "ANTHROPIC_API_KEY",
            "ak",
            "ANTHROPIC_MODEL",
            "claude-x",
            "agents.providers.anthropic_provider.AnthropicProvider",
        ),
    ],
)
def test_llm_factory_create_from_env(
    monkeypatch: pytest.MonkeyPatch,
    env_provider: str,
    env_key: str,
    env_val: str,
    model_env: str,
    model_val: str,
    expected_cls_path: str,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", env_provider)
    monkeypatch.setenv(env_key, env_val)
    monkeypatch.setenv(model_env, model_val)
    if "gemini" in expected_cls_path:
        with patch("agents.providers.gemini_provider.genai") as g:
            g.configure = MagicMock()
            g.GenerativeModel = MagicMock()
            provider = LLMProviderFactory.create_from_env()
        from agents.providers.gemini_provider import GeminiProvider

        assert isinstance(provider, GeminiProvider)
    elif "anthropic" in expected_cls_path:
        with patch("agents.providers.anthropic_provider.anthropic") as a:
            a.Anthropic = MagicMock()
            provider = LLMProviderFactory.create_from_env()
        from agents.providers.anthropic_provider import AnthropicProvider

        assert isinstance(provider, AnthropicProvider)
    elif "groq" in expected_cls_path:
        with patch("agents.providers.groq_provider.Groq"):
            provider = LLMProviderFactory.create_from_env()
        from agents.providers.groq_provider import GroqProvider

        assert isinstance(provider, GroqProvider)
    else:
        with patch("agents.providers.openai_provider.OpenAI"):
            provider = LLMProviderFactory.create_from_env()
        from agents.providers.openai_provider import OpenAIProvider

        assert isinstance(provider, OpenAIProvider)


def _openai_style_response(content: str | None, tool_calls: list | None) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_groq_provider_chat_with_tools_and_bad_json_args() -> None:
    tc_ok = MagicMock()
    tc_ok.id = "c1"
    tc_ok.function.name = "a.b"
    tc_ok.function.arguments = '{"x": 1}'
    tc_bad = MagicMock()
    tc_bad.id = "c2"
    tc_bad.function.name = "a.c"
    tc_bad.function.arguments = "not-json{"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _openai_style_response(None, [tc_ok, tc_bad])
    with patch("agents.providers.groq_provider.Groq", return_value=mock_client):
        from agents.providers.groq_provider import GroqProvider

        p = GroqProvider(api_key="k", model="m")
    msgs = [
        LLMMessage(role="user", content="hi"),
        LLMMessage(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="t1", name="a.b", arguments={"q": 1})],
        ),
        LLMMessage(role="tool", content="{}", tool_call_id="t1", name="a.b"),
    ]
    tools = [{"name": "a.b", "description": "d", "input_schema": {"type": "object"}}]
    out = p.chat_with_tools(msgs, tools)
    assert len(out.tool_calls) == 2
    assert out.tool_calls[0].arguments == {"x": 1}
    assert out.tool_calls[1].arguments == {}


def test_openai_provider_chat_with_tools() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _openai_style_response("done", [])
    with patch("agents.providers.openai_provider.OpenAI", return_value=mock_client):
        from agents.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="k", model="m")
    out = p.chat_with_tools([LLMMessage(role="user", content="hello")], [])
    assert out.content == "done"
    assert out.tool_calls == []


def test_openai_provider_chat_with_tools_bad_json_args() -> None:
    """Invalid JSON in tool arguments becomes empty dict (parity with Groq)."""
    tc_ok = MagicMock()
    tc_ok.id = "c1"
    tc_ok.function.name = "a.b"
    tc_ok.function.arguments = '{"x": 1}'
    tc_bad = MagicMock()
    tc_bad.id = "c2"
    tc_bad.function.name = "a.c"
    tc_bad.function.arguments = "not-json{"
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _openai_style_response(None, [tc_ok, tc_bad])
    with patch("agents.providers.openai_provider.OpenAI", return_value=mock_client):
        from agents.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(api_key="k", model="m")
    out = p.chat_with_tools(
        [LLMMessage(role="user", content="hi")],
        [{"name": "a.b", "description": "d", "input_schema": {"type": "object"}}],
    )
    assert len(out.tool_calls) == 2
    assert out.tool_calls[0].arguments == {"x": 1}
    assert out.tool_calls[1].arguments == {}


def test_anthropic_provider_chat_with_tools() -> None:
    block_tu = MagicMock()
    block_tu.type = "tool_use"
    block_tu.id = "tu1"
    block_tu.name = "fhir_cerner.read_patient"
    block_tu.input = {"resource_id": "1"}
    block_txt = MagicMock()
    block_txt.type = "text"
    block_txt.text = "ok"
    resp = MagicMock()
    resp.content = [block_tu]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = resp
    with patch("agents.providers.anthropic_provider.anthropic") as anth_mod:
        anth_mod.Anthropic = MagicMock(return_value=mock_client)
        from agents.providers.anthropic_provider import AnthropicProvider

        p = AnthropicProvider(api_key="k", model="m")
    out = p.chat_with_tools(
        [LLMMessage(role="user", content="x")],
        [
            {
                "name": "fhir_cerner.read_patient",
                "description": "d",
                "input_schema": {"type": "object"},
            }
        ],
    )
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].name == "fhir_cerner.read_patient"
    assert out.tool_calls[0].arguments == {"resource_id": "1"}


def test_anthropic_provider_tool_use_non_dict_input_becomes_empty_args() -> None:
    block_tu = MagicMock()
    block_tu.type = "tool_use"
    block_tu.id = "tu1"
    block_tu.name = "a.b"
    block_tu.input = "not-a-dict"
    resp = MagicMock()
    resp.content = [block_tu]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = resp
    with patch("agents.providers.anthropic_provider.anthropic") as anth_mod:
        anth_mod.Anthropic = MagicMock(return_value=mock_client)
        from agents.providers.anthropic_provider import AnthropicProvider

        p = AnthropicProvider(api_key="k", model="m")
    out = p.chat_with_tools([LLMMessage(role="user", content="x")], [])
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].arguments == {}


def test_anthropic_provider_mixed_text_and_tool_use() -> None:
    block_txt = MagicMock()
    block_txt.type = "text"
    block_txt.text = "Planning"
    block_tu = MagicMock()
    block_tu.type = "tool_use"
    block_tu.id = "tu1"
    block_tu.name = "a.b"
    block_tu.input = {"q": 1}
    resp = MagicMock()
    resp.content = [block_txt, block_tu]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = resp
    with patch("agents.providers.anthropic_provider.anthropic") as anth_mod:
        anth_mod.Anthropic = MagicMock(return_value=mock_client)
        from agents.providers.anthropic_provider import AnthropicProvider

        p = AnthropicProvider(api_key="k", model="m")
    out = p.chat_with_tools([LLMMessage(role="user", content="go")], [])
    assert out.content == "Planning"
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].arguments == {"q": 1}


def test_mcp_schema_to_gemini_strips_unknown_keys() -> None:
    from agents.providers.gemini_provider import _mcp_schema_to_gemini

    raw = {
        "type": "object",
        "title": "Root",
        "properties": {
            "a": {"type": "string", "x-extra": 1},
        },
        "additionalProperties": False,
    }
    cleaned = _mcp_schema_to_gemini(raw)
    assert "title" not in cleaned
    assert "additionalProperties" not in cleaned
    assert "x-extra" not in cleaned["properties"]["a"]
    assert cleaned["properties"]["a"] == {"type": "string"}


def test_gemini_provider_chat_with_tools() -> None:
    """Inject stub ``google.generativeai.types`` so chat_with_tools can import it."""
    genai_types = types.ModuleType("google.generativeai.types")
    genai_types.FunctionDeclaration = MagicMock
    genai_types.Tool = MagicMock
    sys.modules["google.generativeai.types"] = genai_types

    part_fc = MagicMock()
    part_fc.function_call.name = "google_drive.files.upload"
    part_fc.function_call.args = {"name": "f.txt", "mime_type": "text/plain"}
    type(part_fc).text = property(lambda self: None)
    mock_resp = MagicMock()
    mock_resp.parts = [part_fc]
    mock_chat = MagicMock()
    mock_chat.send_message.return_value = mock_resp
    mock_model = MagicMock()
    mock_model.start_chat.return_value = mock_chat
    try:
        with patch("agents.providers.gemini_provider.genai") as genai_mod:
            genai_mod.configure = MagicMock()
            genai_mod.GenerativeModel.return_value = mock_model
            genai_mod.protos = MagicMock()
            genai_mod.protos.Part = MagicMock(return_value=MagicMock())
            genai_mod.protos.FunctionCall = MagicMock()
            genai_mod.protos.FunctionResponse = MagicMock()
            from agents.providers.gemini_provider import GeminiProvider

            p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
            out = p.chat_with_tools(
                [LLMMessage(role="user", content="upload")],
                [
                    {
                        "name": "google_drive.files.upload",
                        "description": "d",
                        "input_schema": {"type": "object"},
                    }
                ],
            )
        assert len(out.tool_calls) == 1
        assert out.tool_calls[0].name == "google_drive.files.upload"
    finally:
        sys.modules.pop("google.generativeai.types", None)


def test_gemini_provider_text_response_without_tool_calls() -> None:
    genai_types = types.ModuleType("google.generativeai.types")
    genai_types.FunctionDeclaration = MagicMock
    genai_types.Tool = MagicMock
    sys.modules["google.generativeai.types"] = genai_types

    part_txt = MagicMock()
    part_txt.function_call.name = None
    part_txt.text = "Hello from Gemini"
    mock_resp = MagicMock()
    mock_resp.parts = [part_txt]
    mock_chat = MagicMock()
    mock_chat.send_message.return_value = mock_resp
    mock_model = MagicMock()
    mock_model.start_chat.return_value = mock_chat
    try:
        with patch("agents.providers.gemini_provider.genai") as genai_mod:
            genai_mod.configure = MagicMock()
            genai_mod.GenerativeModel.return_value = mock_model
            genai_mod.protos = MagicMock()
            genai_mod.protos.Part = MagicMock(return_value=MagicMock())
            genai_mod.protos.FunctionCall = MagicMock()
            genai_mod.protos.FunctionResponse = MagicMock()
            from agents.providers.gemini_provider import GeminiProvider

            p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
            out = p.chat_with_tools([LLMMessage(role="user", content="hi")], [])
        assert out.content == "Hello from Gemini"
        assert out.tool_calls == []
    finally:
        sys.modules.pop("google.generativeai.types", None)
