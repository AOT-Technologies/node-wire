"""
Tests for the ToolHive Agent and LLM Factory
=============================================
All tests use mocks — no real API keys or ToolHive instance required.

Run:
    pytest tests/test_toolhive_agent.py -v
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.llm_factory import (
    BaseLLMProvider,
    LLMMessage,
    LLMProviderFactory,
    LLMResponse,
    ToolCall,
)
from agents.toolhive import AgentRunResult, ToolHiveAgent, ToolHiveMcpClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TOOLS = [
    {
        "name": "fhir_cerner_read_patient",
        "description": "Fetch a patient from Cerner FHIR",
        "input_schema": {
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "required": ["patient_id"],
        },
    },
    {
        "name": "google_drive_upload_file",
        "description": "Upload a file to Google Drive",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_name": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_name", "content"],
        },
    },
    {
        "name": "smtp_send_email",
        "description": "Send an email via SMTP",
        "input_schema": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to_email", "subject", "body"],
        },
    },
]


def _tool_call(name: str, args: Dict[str, Any]) -> ToolCall:
    return ToolCall(id=str(uuid.uuid4()), name=name, arguments=args)


class _MockLLMProvider(BaseLLMProvider):
    """A mock LLM that replays a pre-configured sequence of responses."""

    def __init__(self, responses: List[LLMResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    def chat_with_tools(self, messages: List[LLMMessage], tools: List[Dict[str, Any]]) -> LLMResponse:
        idx = min(self._call_count, len(self._responses) - 1)
        resp = self._responses[idx]
        self._call_count += 1
        return resp


# ---------------------------------------------------------------------------
# LLM Factory tests
# ---------------------------------------------------------------------------

def test_llm_factory_groq_created() -> None:
    """LLMProviderFactory.create('groq') should return a GroqProvider instance."""
    from agents.llm_factory import LLMProviderFactory
    import agents.providers.groq_provider
    print(f"\nDEBUG: gp file: {agents.providers.groq_provider.__file__}")
    print(f"DEBUG: gp dir: {dir(agents.providers.groq_provider)}")
    with patch("agents.providers.groq_provider.Groq"):
        provider = LLMProviderFactory.create("groq", api_key="test-key", model="llama3-8b-8192")
    from agents.providers.groq_provider import GroqProvider
    assert isinstance(provider, GroqProvider)


def test_llm_factory_openai_created() -> None:
    """LLMProviderFactory.create('openai') should return an OpenAIProvider instance."""
    from agents.llm_factory import LLMProviderFactory
    import agents.providers.openai_provider
    with patch("agents.providers.openai_provider.OpenAI"):
        provider = LLMProviderFactory.create("openai", api_key="test-key", model="gpt-4o-mini")
    from agents.providers.openai_provider import OpenAIProvider
    assert isinstance(provider, OpenAIProvider)


def test_llm_factory_unknown_raises() -> None:
    """LLMProviderFactory.create with an unknown provider should raise ValueError."""
    from agents.llm_factory import LLMProviderFactory
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        LLMProviderFactory.create("foobar")


def test_llm_factory_case_insensitive() -> None:
    """Provider names should be case-insensitive."""
    from agents.llm_factory import LLMProviderFactory
    import agents.providers.groq_provider
    with patch("agents.providers.groq_provider.Groq"):
        provider = LLMProviderFactory.create("GROQ", api_key="k", model="m")
    from agents.providers.groq_provider import GroqProvider
    assert isinstance(provider, GroqProvider)


# ---------------------------------------------------------------------------
# ToolHive Agent tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_runs_three_tool_sequence() -> None:
    """
    Mock LLM returns 3 sequential tool calls, then a final answer.
    All 3 tools should be invoked in order.
    """
    responses = [
        # Step 1: Call FHIR
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("fhir_cerner_read_patient", {"patient_id": "12724066"})],
            stop_reason="tool_calls",
        ),
        # Step 2: Call Drive
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive_upload_file", {"file_name": "summary.txt", "content": "Patient: John"})],
            stop_reason="tool_calls",
        ),
        # Step 3: Send email
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("smtp_send_email", {"to_email": "doc@example.com", "subject": "Summary", "body": "Patient: John"})],
            stop_reason="tool_calls",
        ),
        # Final answer
        LLMResponse(content="All 3 steps completed successfully.", tool_calls=[], stop_reason="stop"),
    ]

    provider = _MockLLMProvider(responses)

    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.return_value = '{"status": "ok"}'

    agent = ToolHiveAgent(mcp_client=mock_mcp, llm_provider=provider, max_steps=10)
    result = await agent.run("Fetch patient 12724066, write to Drive, then email doc@example.com")

    assert result.success is True
    assert result.final_answer == "All 3 steps completed successfully."
    assert len(result.steps) == 3
    assert result.steps[0].tool_called == "fhir_cerner_read_patient"
    assert result.steps[1].tool_called == "google_drive_upload_file"
    assert result.steps[2].tool_called == "smtp_send_email"

    # Verify MCP was called exactly 3 times
    assert mock_mcp.call_tool.await_count == 3


@pytest.mark.asyncio
async def test_agent_respects_max_steps() -> None:
    """Agent should stop and return an error if max_steps is reached."""
    # LLM always returns a tool call — never finishes
    infinite_response = LLMResponse(
        content=None,
        tool_calls=[_tool_call("fhir_cerner_read_patient", {"patient_id": "x"})],
        stop_reason="tool_calls",
    )
    provider = _MockLLMProvider([infinite_response])

    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.return_value = '{"patient_id": "x"}'

    agent = ToolHiveAgent(mcp_client=mock_mcp, llm_provider=provider, max_steps=3)
    result = await agent.run("Keep fetching patient forever")

    assert result.success is False
    assert result.error is not None
    assert "max_steps" in result.error
    assert len(result.steps) == 3  # exactly max_steps tool invocations


@pytest.mark.asyncio
async def test_agent_handles_tool_error_gracefully() -> None:
    """When a tool call raises an exception, the agent records the error and continues."""
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("fhir_cerner_read_patient", {"patient_id": "bad"})],
            stop_reason="tool_calls",
        ),
        LLMResponse(content="Unable to fetch patient — error recorded.", tool_calls=[], stop_reason="stop"),
    ]
    provider = _MockLLMProvider(responses)

    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.side_effect = RuntimeError("FHIR 404 Not Found")

    agent = ToolHiveAgent(mcp_client=mock_mcp, llm_provider=provider, max_steps=5)
    result = await agent.run("Fetch patient bad")

    assert result.success is True  # LLM recovered with a final answer
    assert len(result.steps) == 1
    assert "ERROR" in (result.steps[0].tool_result or "")


@pytest.mark.asyncio
async def test_agent_fails_when_mcp_unreachable() -> None:
    """If list_tools raises, the agent should return a failed result immediately."""
    provider = _MockLLMProvider([])
    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.side_effect = ConnectionError("ToolHive not running")

    agent = ToolHiveAgent(mcp_client=mock_mcp, llm_provider=provider)
    result = await agent.run("Do anything")

    assert result.success is False
    assert "Failed to list MCP tools" in (result.error or "")


# ---------------------------------------------------------------------------
# MCP entrypoint smoke test
# ---------------------------------------------------------------------------

def test_mcp_entrypoint_registers_eight_tools() -> None:
    """The FastMCP server should expose the full FHIR + integration tool surface."""
    # We patch all external deps before importing the module to avoid side effects
    with (
        patch("bindings.factory.ConnectorFactory") as mock_factory_cls,
        patch("connectors.auto_register"),
        patch("mcp.server.fastmcp.FastMCP", autospec=False) as mock_fastmcp_cls,
    ):
        mock_factory = MagicMock()
        mock_factory._connectors = {
            "fhir_cerner": MagicMock(),
            "fhir_epic": MagicMock(),
            "google_drive": MagicMock(),
            "smtp": MagicMock(),
        }
        mock_factory_cls.return_value = mock_factory

        mock_mcp_instance = MagicMock()
        registered_tools: List[str] = []

        def fake_tool(*args: Any, **kwargs: Any):
            name = kwargs.get("name") or (args[0] if args else "unknown")
            registered_tools.append(name)
            return lambda fn: fn  # decorator passthrough

        mock_mcp_instance.tool = fake_tool
        mock_fastmcp_cls.return_value = mock_mcp_instance

        # Import inside the test to ensure it picks up the mocks
        from agents.mcp_entrypoint import _make_server
        _make_server()

    assert len(registered_tools) == 8
    assert "fhir_cerner_read_patient" in registered_tools
    assert "fhir_cerner_search_patients" in registered_tools
    assert "fhir_cerner_search_encounters" in registered_tools
    assert "fhir_epic_read_patient" in registered_tools
    assert "fhir_epic_search_patients" in registered_tools
    assert "fhir_epic_search_encounters" in registered_tools
    assert "google_drive_upload_file" in registered_tools
    assert "smtp_send_email" in registered_tools


# ---------------------------------------------------------------------------
# Individual MCP server smoke tests
# ---------------------------------------------------------------------------

def _make_server_smoke(module_path: str, expected_tool: str) -> None:
    """Helper: verify a per-connector _make_server() registers exactly one tool."""
    with (
        patch("bindings.factory.ConnectorFactory") as mock_factory_cls,
        patch("connectors.auto_register"),
        patch("mcp.server.fastmcp.FastMCP", autospec=False) as mock_fastmcp_cls,
    ):
        mock_factory = MagicMock()
        mock_factory._connectors = {}
        mock_factory_cls.return_value = mock_factory

        mock_mcp_instance = MagicMock()
        registered_tools: List[str] = []

        def fake_tool(*args: Any, **kwargs: Any):
            name = kwargs.get("name") or (args[0] if args else "unknown")
            registered_tools.append(name)
            return lambda fn: fn

        mock_mcp_instance.tool = fake_tool
        mock_fastmcp_cls.return_value = mock_mcp_instance

        import importlib
        mod = importlib.import_module(module_path)
        mod._make_server()

    assert registered_tools == [expected_tool], (
        f"{module_path}: expected [{expected_tool}], got {registered_tools}"
    )


def test_fhir_cerner_mcp_registers_one_tool() -> None:
    """fhir_cerner_mcp._make_server() should expose exactly fhir_cerner_read_patient."""
    _make_server_smoke("agents.fhir_cerner_mcp", "fhir_cerner_read_patient")


def test_fhir_epic_mcp_registers_one_tool() -> None:
    """fhir_epic_mcp._make_server() should expose exactly fhir_epic_read_patient."""
    _make_server_smoke("agents.fhir_epic_mcp", "fhir_epic_read_patient")


def test_google_drive_mcp_registers_one_tool() -> None:
    """google_drive_mcp._make_server() should expose exactly google_drive_upload_file."""
    _make_server_smoke("agents.google_drive_mcp", "google_drive_upload_file")


