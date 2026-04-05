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
from agents.toolhive import (
    AgentRunResult,
    ToolHiveAgent,
    ToolHiveMcpClient,
    _is_tool_failure,
    resolve_max_tool_failures,
    truncate_tool_result_for_llm,
)


def test_truncate_tool_result_for_llm_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLHIVE_MAX_TOOL_RESULT_CHARS", "20")
    long = "x" * 100
    out = truncate_tool_result_for_llm(long)
    assert len(out) > 20
    assert out.startswith("x" * 20)
    assert "truncated" in out


def test_truncate_tool_result_for_llm_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLHIVE_MAX_TOOL_RESULT_CHARS", "0")
    long = "y" * 5000
    assert truncate_tool_result_for_llm(long) == long


def test_is_tool_failure_detects_validation_and_error_prefix() -> None:
    assert _is_tool_failure("Input validation error: bad")
    assert _is_tool_failure("ERROR: connection refused")
    assert _is_tool_failure('{"success": false, "message": "x"}')
    assert not _is_tool_failure("")
    assert not _is_tool_failure('{"success": true, "data": {}}')


def test_resolve_max_tool_failures_env_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOOLHIVE_MAX_TOOL_FAILURES", raising=False)
    assert resolve_max_tool_failures(None) == 2
    monkeypatch.setenv("TOOLHIVE_MAX_TOOL_FAILURES", "5")
    assert resolve_max_tool_failures(None) == 5
    assert resolve_max_tool_failures(3) == 3


# ---------------------------------------------------------------------------
# Fixtures (manifest-driven — must match production tools/list)
# ---------------------------------------------------------------------------

def _mcp_tools_subset_from_manifest() -> List[Dict[str, Any]]:
    """Same input_schema as McpServer.list_tools for a stable agent-test subset."""
    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.manifest import build_manifest

    auto_register()
    factory = ConnectorFactory()
    factory.load()
    manifest = build_manifest(factory.list_for_protocol("mcp"))
    want = {"fhir_cerner.read_patient", "google_drive.files.upload", "smtp.send_email"}
    out: List[Dict[str, Any]] = []
    for entry in manifest:
        name = f"{entry['connector_id']}.{entry['action']}"
        if name in want:
            out.append(
                {
                    "name": name,
                    "description": f"{entry['connector_id']} {entry['action']}",
                    "input_schema": entry["input_schema"],
                }
            )
    assert {t["name"] for t in out} == want
    return sorted(out, key=lambda t: t["name"])


SAMPLE_TOOLS = _mcp_tools_subset_from_manifest()


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
            tool_calls=[_tool_call("fhir_cerner.read_patient", {"resource_id": "12724066"})],
            stop_reason="tool_calls",
        ),
        # Step 2: Call Drive
        LLMResponse(
            content=None,
            tool_calls=[
                _tool_call(
                    "google_drive.files.upload",
                    {
                        "name": "summary.txt",
                        "mime_type": "text/plain",
                        "content": "Patient: John",
                    },
                )
            ],
            stop_reason="tool_calls",
        ),
        # Step 3: Send email
        LLMResponse(
            content=None,
            tool_calls=[
                _tool_call(
                    "smtp.send_email",
                    {
                        "to": ["doc@example.com"],
                        "subject": "Summary",
                        "body": "Patient: John",
                    },
                )
            ],
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
    assert result.steps[0].tool_called == "fhir_cerner.read_patient"
    assert result.steps[1].tool_called == "google_drive.files.upload"
    assert result.steps[2].tool_called == "smtp.send_email"

    # Verify MCP was called exactly 3 times
    assert mock_mcp.call_tool.await_count == 3


@pytest.mark.asyncio
async def test_agent_id_first_turn_calls_read_patient_with_resource_id() -> None:
    """Document ID-first flow: Cerner read uses canonical resource_id (not search_patients)."""
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("fhir_cerner.read_patient", {"resource_id": "12724066"})],
            stop_reason="tool_calls",
        ),
        LLMResponse(content="Patient retrieved.", tool_calls=[], stop_reason="stop"),
    ]
    provider = _MockLLMProvider(responses)
    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.return_value = '{"success": true}'

    agent = ToolHiveAgent(mcp_client=mock_mcp, llm_provider=provider, max_steps=10)
    result = await agent.run("Patient ID 12724066 — fetch from Cerner")

    assert result.success is True
    mock_mcp.call_tool.assert_awaited_once()
    call = mock_mcp.call_tool.await_args
    assert call[0][0] == "fhir_cerner.read_patient"
    assert call[0][1]["resource_id"] == "12724066"


@pytest.mark.asyncio
async def test_agent_respects_max_steps() -> None:
    """Agent should stop and return an error if max_steps is reached."""
    # LLM always returns a tool call — never finishes
    infinite_response = LLMResponse(
        content=None,
        tool_calls=[_tool_call("fhir_cerner.read_patient", {"resource_id": "x"})],
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
            tool_calls=[_tool_call("fhir_cerner.read_patient", {"resource_id": "bad"})],
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


@pytest.mark.asyncio
async def test_agent_stops_after_repeated_tool_failures() -> None:
    """After max_tool_failures for the same tool, stop without further LLM steps."""
    fail_msg = "Input validation error: bad args"
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive.files.upload", {"name": "a.txt"})],
            stop_reason="tool_calls",
        ),
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive.files.upload", {"name": "a.txt"})],
            stop_reason="tool_calls",
        ),
        LLMResponse(content="should not run", tool_calls=[], stop_reason="stop"),
    ]
    provider = _MockLLMProvider(responses)
    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.return_value = fail_msg

    agent = ToolHiveAgent(
        mcp_client=mock_mcp,
        llm_provider=provider,
        max_steps=10,
        max_tool_failures=2,
    )
    result = await agent.run("Upload to Drive")

    assert result.success is False
    assert len(result.steps) == 2
    assert "google_drive.files.upload" in (result.error or "")
    assert "failed 2 times" in (result.final_answer or result.error or "").lower()
    assert mock_mcp.call_tool.await_count == 2
    assert provider._call_count == 2


@pytest.mark.asyncio
async def test_agent_success_then_two_failures_same_tool_aborts() -> None:
    """Failures only increment on failed tool results; abort after second failure."""
    ok = '{"success": true, "data": {}}'
    fail_msg = "Input validation error: x"
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive.files.upload", {})],
            stop_reason="tool_calls",
        ),
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive.files.upload", {})],
            stop_reason="tool_calls",
        ),
        LLMResponse(
            content=None,
            tool_calls=[_tool_call("google_drive.files.upload", {})],
            stop_reason="tool_calls",
        ),
    ]
    provider = _MockLLMProvider(responses)
    mock_mcp = AsyncMock(spec=ToolHiveMcpClient)
    mock_mcp.list_tools.return_value = SAMPLE_TOOLS
    mock_mcp.call_tool.side_effect = [ok, fail_msg, fail_msg]

    agent = ToolHiveAgent(
        mcp_client=mock_mcp,
        llm_provider=provider,
        max_steps=10,
        max_tool_failures=2,
    )
    result = await agent.run("x")

    assert result.success is False
    assert len(result.steps) == 3
    assert mock_mcp.call_tool.await_count == 3


# ---------------------------------------------------------------------------
# MCP entrypoint smoke test
# ---------------------------------------------------------------------------

def test_mcp_entrypoint_exposes_manifest_tools() -> None:
    """Unified MCP server lists all connectors enabled for MCP in config."""
    from bindings.mcp_server.server import McpServer

    server = McpServer(server_name="node-wire")
    names = {t["name"] for t in server.list_tools()}
    assert "fhir_cerner.read_patient" in names
    assert "fhir_epic.read_patient" in names
    assert "google_drive.files.upload" in names
    assert "smtp.send_email" in names
    assert "stripe.charge" in names
    assert "http_generic.request" in names
    # Broader surface than the old 8 FastMCP tools
    assert len(names) >= 18


# ---------------------------------------------------------------------------
# Individual MCP entrypoint modules (thin wrappers)
# ---------------------------------------------------------------------------


def test_fhir_cerner_mcp_main_callable() -> None:
    from agents.fhir_cerner_mcp import main

    assert callable(main)


def test_fhir_epic_mcp_main_callable() -> None:
    from agents.fhir_epic_mcp import main

    assert callable(main)


def test_google_drive_mcp_main_callable() -> None:
    from agents.google_drive_mcp import main

    assert callable(main)


def test_smtp_mcp_main_callable() -> None:
    from agents.smtp_mcp import main

    assert callable(main)


def test_mcp_server_matches_per_connector_entrypoints() -> None:
    """Per-connector scripts use connector_ids filter; tool prefixes must match."""
    from bindings.mcp_server.server import McpServer

    full = {t["name"] for t in McpServer().list_tools()}

    cerner = {t["name"] for t in McpServer(connector_ids=["fhir_cerner"]).list_tools()}
    assert cerner == {n for n in full if n.startswith("fhir_cerner.")}

    epic = {t["name"] for t in McpServer(connector_ids=["fhir_epic"]).list_tools()}
    assert epic == {n for n in full if n.startswith("fhir_epic.")}

    drive = {t["name"] for t in McpServer(connector_ids=["google_drive"]).list_tools()}
    assert drive == {n for n in full if n.startswith("google_drive.")}
    assert "google_drive.files.upload" in drive

    smtp = {t["name"] for t in McpServer(connector_ids=["smtp"]).list_tools()}
    assert smtp == {"smtp.send_email"}


