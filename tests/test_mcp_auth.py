from __future__ import annotations

import jwt
import pytest

from bindings.mcp_server.auth import (
    McpAuthInvalidError,
    McpAuthRequiredError,
    authenticate_mcp_request,
)
from bindings.mcp_server.server import McpServer


@pytest.fixture(autouse=True)
def _mcp_auth_clear_allowlist_from_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host ``NW_ALLOWED_CONNECTORS`` (e.g. from .env) can drop ``smtp`` from the registry."""
    monkeypatch.delenv("NW_ALLOWED_CONNECTORS", raising=False)


def test_mcp_auth_missing_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    with pytest.raises(McpAuthRequiredError) as exc_info:
        authenticate_mcp_request()
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_mcp_auth_invalid_token_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    with pytest.raises(McpAuthInvalidError) as exc_info:
        authenticate_mcp_request(meta={"token": "wrong-secret"})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid API key or token"


def test_mcp_auth_valid_token_allows_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    identity = authenticate_mcp_request(meta={"token": "unit-test-secret"})
    assert identity is not None

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert any(t["name"] == "smtp.send_email" for t in tools)


@pytest.mark.asyncio
async def test_mcp_authz_denies_tool_without_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    token = jwt.encode(
        {"sub": "alice", "tenant_id": "tenant-a", "scopes": ["mcp:other.scope"]},
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})
    assert identity is not None

    server = McpServer(connector_ids=["smtp"])
    resp = await server.invoke_tool(
        "smtp.send_email",
        {
            "from_email": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "x",
            "body": "y",
        },
        identity=identity,
    )

    assert resp["success"] is False
    assert resp["error_code"] == "POLICY_DENIED"
    assert resp["message"] == "Missing required scope: mcp:smtp.send_email"


@pytest.mark.asyncio
async def test_mcp_execution_passes_principal_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)

    token = jwt.encode(
        {"sub": "service-account", "tenant_id": "tenant-42", "scopes": ["*"]},
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})
    assert identity is not None

    server = McpServer(connector_ids=["smtp"])
    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["payload"] = dict(raw_input)
        captured["principal"] = principal
        captured["tenant_id"] = tenant_id
        captured["scopes"] = tuple(scopes or ())
        from node_wire_runtime.models import ConnectorResponse

        return ConnectorResponse(success=True, data={"ok": True}, trace_id="trace-test")

    orig_run = smtp.run
    try:
        smtp.run = fake_run
        await server.invoke_tool(
            "smtp.send_email",
            {
                "from_email": "sender@example.com",
                "to": ["recipient@example.com"],
                "subject": "x",
                "body": "y",
            },
            identity=identity,
        )
    finally:
        smtp.run = orig_run

    assert captured["principal"] == "service-account"
    assert captured["tenant_id"] == "tenant-42"
    assert captured["scopes"] == ("*",)


def test_mcp_api_key_scopes_filter_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )
    monkeypatch.setenv("NW_MCP_API_KEY_SCOPES", "mcp:other.scope")

    identity = authenticate_mcp_request(meta={"token": "unit-test-secret"})
    assert identity is not None
    assert identity.scopes == ("mcp:other.scope",)

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert not any(t["name"] == "smtp.send_email" for t in tools)


def test_mcp_jwt_scopes_filter_tools_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    token = jwt.encode(
        {"sub": "alice", "scopes": ["mcp:other.scope"]},
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})
    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert not any(t["name"] == "smtp.send_email" for t in tools)


@pytest.mark.asyncio
async def test_mcp_default_deny_fallback_scope_invokes_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "deny")

    token = jwt.encode(
        {"sub": "bob", "scopes": ["mcp:smtp.send_email"]},
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert any(t["name"] == "smtp.send_email" for t in tools)

    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        from node_wire_runtime.models import ConnectorResponse

        assert scopes == ("mcp:smtp.send_email",)
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="trace-test")

    orig_run = smtp.run
    try:
        smtp.run = fake_run
        resp = await server.invoke_tool(
            "smtp.send_email",
            {
                "from_email": "sender@example.com",
                "to": ["recipient@example.com"],
                "subject": "x",
                "body": "y",
            },
            identity=identity,
        )
    finally:
        smtp.run = orig_run

    assert resp["success"] is True


@pytest.mark.asyncio
async def test_mcp_default_deny_denies_without_fallback_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "deny")

    token = jwt.encode(
        {"sub": "bob", "scopes": ["mcp:wrong.scope"]},
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert not any(t["name"] == "smtp.send_email" for t in tools)

    resp = await server.invoke_tool(
        "smtp.send_email",
        {
            "from_email": "sender@example.com",
            "to": ["recipient@example.com"],
            "subject": "x",
            "body": "y",
        },
        identity=identity,
    )
    assert resp["success"] is False
    assert resp["error_code"] == "POLICY_DENIED"


def test_mcp_api_key_explicit_star_scope_lists_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )
    monkeypatch.setenv("NW_MCP_API_KEY_SCOPES", "*")

    identity = authenticate_mcp_request(meta={"token": "unit-test-secret"})
    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools(identity=identity)
    assert any(t["name"] == "smtp.send_email" for t in tools)
