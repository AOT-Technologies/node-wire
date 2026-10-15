#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Playground LLM switcher phase 2 (Ollama discover + base_url)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _phase2_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")


def test_llm_discover_ollama_returns_models(monkeypatch: pytest.MonkeyPatch) -> None:
    # The operator-configured Ollama is the one private address discovery may reach.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "models": [{"name": "qwen2.5:7b"}, {"name": "llama3:latest"}]
    }

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from bindings.rest_api.app import app

        client = TestClient(app)
        resp = client.get(
            "/scenarios/llm-discover-ollama",
            params={"base_url": "http://127.0.0.1:11434"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == ["llama3:latest", "qwen2.5:7b"]
    assert data["base_url"] == "http://127.0.0.1:11434/v1"
    assert data["error"] is None


@pytest.mark.parametrize(
    ("configured", "requested"),
    [
        ("http://127.0.0.1:11434/v1", "http://169.254.169.254"),  # cloud metadata
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:8080"),  # another local port
        ("http://127.0.0.1:11434/v1", "http://10.0.0.5:11434"),  # private network
        ("", "http://127.0.0.1:8080"),  # nothing configured: only the default Ollama is allowed
    ],
)
def test_llm_discover_ollama_blocks_internal_destinations(
    monkeypatch: pytest.MonkeyPatch, configured: str, requested: str
) -> None:
    if configured:
        monkeypatch.setenv("OLLAMA_BASE_URL", configured)
    else:
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    with patch("httpx.AsyncClient") as client_cls:
        from bindings.rest_api.app import app

        resp = TestClient(app).get("/scenarios/llm-discover-ollama", params={"base_url": requested})

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["base_url"] is None
    assert "Blocked destination" in data["error"]
    client_cls.assert_not_called()  # nothing was fetched


def test_llm_discover_ollama_allows_the_default_local_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no OLLAMA_BASE_URL set, the built-in local default still works for the UI."""
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        from bindings.rest_api.app import app

        resp = TestClient(app).get(
            "/scenarios/llm-discover-ollama", params={"base_url": "http://127.0.0.1:11434"}
        )

    assert resp.json()["models"] == ["qwen2.5:7b"]
    assert resp.json()["error"] is None


def test_llm_discover_ollama_rejects_invalid_scheme() -> None:
    from bindings.rest_api.app import app

    client = TestClient(app)
    resp = client.get(
        "/scenarios/llm-discover-ollama",
        params={"base_url": "ftp://127.0.0.1:11434"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["base_url"] is None
    assert data["error"] == "Invalid Ollama URL. Use http or https."


def test_agent_chat_forwards_llm_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama")
    monkeypatch.setenv("NW_MCP_TRANSPORT", "stdio")

    from agents.toolhive import AgentRunResult

    created: list[tuple[str | None, str | None]] = []

    class FakeProvider:
        def chat_with_tools(self, messages, tools):  # noqa: ANN001
            return None

    def fake_create_from_option(llm_option=None, base_url=None):
        created.append((llm_option, base_url))
        return FakeProvider()

    async def fake_run(self, task):
        return AgentRunResult(
            success=True, final_answer="hello from ollama", steps=[], trace_id="t1"
        )

    class _CM:
        async def __aenter__(self):
            client = MagicMock()
            client._server = None
            return client

        async def __aexit__(self, *args):
            return None

    with (
        patch(
            "agents.llm_factory.LLMProviderFactory.create_from_option",
            side_effect=fake_create_from_option,
        ),
        patch("agents.toolhive.ToolHiveAgent.run", fake_run),
        patch(
            "playground.scenarios._playground_inprocess_mcp_client",
            return_value=_CM(),
        ),
    ):
        from bindings.rest_api.app import app

        client = TestClient(app)
        resp = client.post(
            "/scenarios/agent-chat",
            json={
                "message": "hi",
                "history": [],
                "llm_option": "ollama/qwen2.5:7b",
                "llm_base_url": "http://127.0.0.1:11434/v1",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["reply"] == "hello from ollama"
    assert created == [("ollama/qwen2.5:7b", "http://127.0.0.1:11434/v1")]


def test_agent_chat_rejects_invalid_llm_base_url() -> None:
    from bindings.rest_api.app import app

    client = TestClient(app)
    resp = client.post(
        "/scenarios/agent-chat",
        json={
            "message": "hi",
            "history": [],
            "llm_option": "ollama/qwen2.5:7b",
            "llm_base_url": "ftp://127.0.0.1:11434/v1",
        },
    )
    assert resp.status_code == 400
