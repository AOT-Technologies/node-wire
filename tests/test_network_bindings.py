#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bindings.grpc_server.tls_config import (
    configure_grpc_server_port,
    is_public_bind_host as grpc_is_public_bind_host,
    resolve_grpc_host,
)
from bindings.mcp_server.server import (
    McpServer,
    is_public_bind_host,
    resolve_mcp_host,
)


def test_resolve_mcp_host_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_HOST", raising=False)
    assert resolve_mcp_host() == "127.0.0.1"


def test_resolve_mcp_host_respects_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_HOST", "0.0.0.0")
    assert resolve_mcp_host() == "0.0.0.0"


def test_resolve_mcp_host_accepts_explicit_override() -> None:
    assert resolve_mcp_host("10.0.0.5") == "10.0.0.5"


def test_is_public_bind_host() -> None:
    assert is_public_bind_host("0.0.0.0")
    assert is_public_bind_host("::")
    assert not is_public_bind_host("127.0.0.1")
    assert not is_public_bind_host("10.0.0.1")


@pytest.mark.asyncio
async def test_mcp_public_bind_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("NW_MCP_HOST", "0.0.0.0")
    server = McpServer()
    mock_uvicorn_server = MagicMock()
    mock_uvicorn_server.serve = AsyncMock()

    with (
        patch.object(server, "_setup_lowlevel_server", return_value=MagicMock()),
        patch.object(server, "_build_streamable_http_app", return_value=MagicMock()),
        patch("uvicorn.Config", return_value=MagicMock()),
        patch("uvicorn.Server", return_value=mock_uvicorn_server),
        caplog.at_level("WARNING", logger="bindings.mcp_server"),
    ):
        await server._run_streamable_http_async()

    assert any("binding to all interfaces" in record.message for record in caplog.records)
    mock_uvicorn_server.serve.assert_awaited_once()


def test_grpc_configure_secure_when_tls_present(tmp_path) -> None:
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(b"cert-bytes")
    key_path.write_bytes(b"key-bytes")
    grpc_server = MagicMock()

    with patch("bindings.grpc_server.tls_config.grpc.ssl_server_credentials") as mock_creds:
        mode = configure_grpc_server_port(
            grpc_server,
            port=50051,
            cert_path=str(cert_path),
            key_path=str(key_path),
            require_tls=False,
        )

    assert mode == "secure"
    grpc_server.add_secure_port.assert_called_once()
    grpc_server.add_insecure_port.assert_not_called()
    mock_creds.assert_called_once()


def test_grpc_configure_insecure_when_tls_absent() -> None:
    grpc_server = MagicMock()
    mode = configure_grpc_server_port(
        grpc_server,
        port=50051,
        cert_path=None,
        key_path=None,
        require_tls=False,
    )
    assert mode == "insecure"
    # Default bind host is now loopback (parity with MCP), not all-interfaces.
    grpc_server.add_insecure_port.assert_called_once_with("127.0.0.1:50051")
    grpc_server.add_secure_port.assert_not_called()


def test_resolve_grpc_host_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_GRPC_HOST", raising=False)
    assert resolve_grpc_host() == "127.0.0.1"


def test_resolve_grpc_host_respects_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_GRPC_HOST", "::")
    assert resolve_grpc_host() == "::"


def test_grpc_is_public_bind_host() -> None:
    assert grpc_is_public_bind_host("0.0.0.0")
    assert grpc_is_public_bind_host("::")
    assert not grpc_is_public_bind_host("127.0.0.1")


def test_grpc_ipv6_host_is_bracketed(monkeypatch: pytest.MonkeyPatch) -> None:
    grpc_server = MagicMock()
    configure_grpc_server_port(
        grpc_server,
        port=50051,
        host="::",
        cert_path=None,
        key_path=None,
        require_tls=False,
    )
    grpc_server.add_insecure_port.assert_called_once_with("[::]:50051")


def test_grpc_public_bind_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    grpc_server = MagicMock()
    with caplog.at_level("WARNING", logger="bindings.grpc_server"):
        configure_grpc_server_port(
            grpc_server,
            port=50051,
            host="0.0.0.0",
            cert_path=None,
            key_path=None,
            require_tls=False,
        )
    assert any("binding to all interfaces" in record.message for record in caplog.records)


def test_grpc_require_tls_raises_without_creds() -> None:
    grpc_server = MagicMock()
    with pytest.raises(ValueError, match="NW_GRPC_REQUIRE_TLS"):
        configure_grpc_server_port(
            grpc_server,
            port=50051,
            cert_path=None,
            key_path=None,
            require_tls=True,
        )
    grpc_server.add_insecure_port.assert_not_called()
    grpc_server.add_secure_port.assert_not_called()


def test_grpc_partial_tls_treated_as_missing_when_require_tls() -> None:
    grpc_server = MagicMock()
    with pytest.raises(ValueError, match="NW_GRPC_TLS_CERT_PATH"):
        configure_grpc_server_port(
            grpc_server,
            port=50051,
            cert_path="/tmp/cert.pem",
            key_path=None,
            require_tls=True,
        )


def test_grpc_partial_tls_starts_insecure_when_not_required() -> None:
    grpc_server = MagicMock()
    mode = configure_grpc_server_port(
        grpc_server,
        port=50051,
        cert_path="/tmp/cert.pem",
        key_path=None,
        require_tls=False,
    )
    assert mode == "insecure"
    grpc_server.add_insecure_port.assert_called_once()
