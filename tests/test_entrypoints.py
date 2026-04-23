"""Tests for MCP and REST/gRPC process entrypoints."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def test_mcp_entrypoint_main_calls_run_stdio() -> None:
    with patch("bindings.mcp_server.server.McpServer") as MockServer:
        from agents import mcp_entrypoint

        mcp_entrypoint.main()
        MockServer.assert_called_once_with(server_name="node-wire")
        MockServer.return_value.run.assert_called_once()


@pytest.mark.parametrize(
    "module_path",
    [
        "agents.fhir_cerner_mcp",
        "agents.fhir_epic_mcp",
        "agents.google_drive_mcp",
        "agents.smtp_mcp",
    ],
)
def test_per_connector_mcp_main_calls_run_stdio(module_path: str) -> None:
    with patch("bindings.mcp_server.server.McpServer") as MockServer:
        mod = __import__(module_path, fromlist=["main"])
        mod.main()
        MockServer.return_value.run.assert_called_once()


def test_bindings_entrypoint_api_mode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODE", raising=False)
    with (
        patch("bindings_entrypoint.init_observability") as mock_obs,
        patch("bindings_entrypoint.uvicorn.run") as mock_uv,
    ):
        import bindings_entrypoint

        bindings_entrypoint.main()
    mock_obs.assert_called_once_with(app_name="node-wire")
    mock_uv.assert_called_once()
    call_kw = mock_uv.call_args[1]
    assert call_kw["host"] == "0.0.0.0"
    assert call_kw["port"] == 8000


def test_bindings_entrypoint_api_mode_explicit_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "API")
    monkeypatch.setenv("PORT", "9000")
    with (
        patch("bindings_entrypoint.init_observability"),
        patch("bindings_entrypoint.uvicorn.run") as mock_uv,
    ):
        import bindings_entrypoint

        bindings_entrypoint.main()
    assert mock_uv.call_args[1]["port"] == 9000


def test_bindings_entrypoint_grpc_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """GRPC path lazy-imports `serve`; stub the module so generated protos are not required."""
    monkeypatch.setenv("MODE", "GRPC")
    mock_serve = MagicMock()
    fake_grpc_server = MagicMock()
    fake_grpc_server.serve = mock_serve
    with (
        patch.dict(sys.modules, {"bindings.grpc_server.server": fake_grpc_server}),
        patch("bindings_entrypoint.init_observability"),
    ):
        import bindings_entrypoint

        bindings_entrypoint.main()
    mock_serve.assert_called_once_with(port=50051)


def test_bindings_entrypoint_mcp_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "MCP")
    mock_server = MagicMock()
    mock_server.list_tools.return_value = [{"name": "a.b"}]
    with (
        patch("bindings_entrypoint.init_observability"),
        patch("bindings_entrypoint.McpServer", return_value=mock_server),
        patch("time.sleep", side_effect=RuntimeError("stop_loop")),
    ):
        import bindings_entrypoint

        with pytest.raises(RuntimeError, match="stop_loop"):
            bindings_entrypoint.main()
    mock_server.list_tools.assert_called()


def test_bindings_entrypoint_unknown_mode_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODE", "NOT_A_MODE")
    with (
        patch("bindings_entrypoint.init_observability"),
        pytest.raises(SystemExit, match="Unknown MODE"),
    ):
        import bindings_entrypoint

        bindings_entrypoint.main()
