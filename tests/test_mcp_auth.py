from __future__ import annotations

from contextlib import asynccontextmanager

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from bindings.mcp_server.auth import (
    McpAuthInvalidError,
    McpAuthRequiredError,
    authenticate_mcp_request,
    mcp_auth_enabled,
)
from bindings.mcp_server.server import McpServer


@pytest.fixture(autouse=True)
def _mcp_auth_clear_allowlist_from_host_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin allowlist + scope defaults: host ``.env`` or deny-default leaks empty API-key scopes."""
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS",
        "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner",
    )
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY_SCOPES", raising=False)
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)


def test_mcp_auth_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_MCP_AUTH_ENABLED", raising=False)
    assert not mcp_auth_enabled()
    assert authenticate_mcp_request() is None


def test_mcp_auth_missing_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    with pytest.raises(McpAuthRequiredError) as exc_info:
        authenticate_mcp_request()
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"


def test_mcp_auth_invalid_token_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    with pytest.raises(McpAuthInvalidError) as exc_info:
        authenticate_mcp_request(meta={"token": "wrong-secret"})
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid API key or token"


def test_mcp_tools_list_is_public_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools()
    assert any(t["name"] == "smtp.send_email" for t in tools)


def test_mcp_jwt_parses_blocked_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")

    token = jwt.encode(
        {
            "sub": "alice",
            "scopes": ["mcp:smtp.send_email"],
            "blocked_scopes": ["mcp:smtp.send_email"],
        },
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})
    assert identity is not None
    assert identity.scopes == ("mcp:smtp.send_email",)
    assert identity.blocked_scopes == ("mcp:smtp.send_email",)


@pytest.mark.asyncio
async def test_mcp_authz_denies_tool_without_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
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
async def test_mcp_blocked_scopes_denies_even_when_scope_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    token = jwt.encode(
        {
            "sub": "alice",
            "scopes": ["mcp:smtp.send_email"],
            "blocked_scopes": ["mcp:smtp.send_email"],
        },
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})

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
    assert resp["message"] == "Scope blocked for this action: mcp:smtp.send_email"


@pytest.mark.asyncio
async def test_mcp_blocked_scopes_denies_wildcard_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    token = jwt.encode(
        {
            "sub": "alice",
            "scopes": ["*"],
            "blocked_scopes": ["mcp:smtp.send_email"],
        },
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})

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
    assert "Scope blocked" in (resp["message"] or "")


@pytest.mark.asyncio
async def test_mcp_execution_passes_principal_and_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)

    token = jwt.encode(
        {
            "sub": "service-account",
            "tenant_id": "tenant-42",
            "scopes": ["*"],
            "blocked_scopes": ["mcp:unused.block"],
        },
        "jwt-secret",
        algorithm="HS256",
    )
    identity = authenticate_mcp_request(meta={"authorization": f"Bearer {token}"})
    assert identity is not None

    server = McpServer(connector_ids=["smtp"])
    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    captured: dict[str, object] = {}

    async def fake_run(
        raw_input,
        *,
        principal=None,
        tenant_id=None,
        scopes=None,
        blocked_scopes=None,
    ):
        captured["payload"] = dict(raw_input)
        captured["principal"] = principal
        captured["tenant_id"] = tenant_id
        captured["scopes"] = tuple(scopes or ())
        captured["blocked_scopes"] = tuple(blocked_scopes or ())
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
    assert captured["blocked_scopes"] == ("mcp:unused.block",)


def test_mcp_tools_list_unfiltered_for_narrow_api_key_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )
    monkeypatch.setenv("NW_MCP_API_KEY_SCOPES", "mcp:other.scope")

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools()
    assert any(t["name"] == "smtp.send_email" for t in tools)


def test_mcp_tools_list_unfiltered_for_narrow_jwt_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    server = McpServer(connector_ids=["smtp"])
    tools = server.list_tools()
    assert any(t["name"] == "smtp.send_email" for t in tools)


@pytest.mark.asyncio
async def test_mcp_default_deny_fallback_scope_invokes_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
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
    assert any(t["name"] == "smtp.send_email" for t in server.list_tools())

    smtp = server._factory.get_for_protocol("smtp", "mcp")
    assert smtp is not None

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None, blocked_scopes=None):
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
async def test_mcp_default_deny_denies_invoke_without_fallback_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
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
    assert any(t["name"] == "smtp.send_email" for t in server.list_tools())

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


class _FakeStreamableSessionManager:
    @asynccontextmanager
    async def run(self):
        yield

    async def handle_request(self, scope, receive, send):
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)


def test_streamable_http_tools_list_public_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(connector_ids=["smtp"])
    app = server._build_streamable_http_app(
        session_manager=_FakeStreamableSessionManager(),
        path="/mcp",
    )
    client = TestClient(app)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": "1", "method": "tools/list"})

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_streamable_http_tools_call_requires_token_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(connector_ids=["smtp"])
    app = server._build_streamable_http_app(
        session_manager=_FakeStreamableSessionManager(),
        path="/mcp",
    )
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "1", "method": "tools/call"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "MCP_AUTH_REQUIRED"


def test_streamable_http_edge_auth_rejects_invalid_token_on_tools_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(connector_ids=["smtp"])
    app = server._build_streamable_http_app(
        session_manager=_FakeStreamableSessionManager(),
        path="/mcp",
    )
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "1", "method": "tools/call"},
        headers={"Authorization": "Bearer wrong-secret"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "MCP_AUTH_INVALID"


def test_streamable_http_edge_auth_accepts_valid_token_on_tools_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(connector_ids=["smtp"])
    app = server._build_streamable_http_app(
        session_manager=_FakeStreamableSessionManager(),
        path="/mcp",
    )
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": "1", "method": "tools/call"},
        headers={"Authorization": "Bearer unit-test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_streamable_http_identity_context_is_used_by_mcp_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_API_KEY", "unit-test-secret")
    monkeypatch.delenv("NW_MCP_JWT_SECRET", raising=False)

    server = McpServer(connector_ids=["smtp"])
    identity = authenticate_mcp_request(meta={"token": "unit-test-secret"})
    assert identity is not None

    from bindings.mcp_server.server import _streamable_http_identity_ctx

    token = _streamable_http_identity_ctx.set(identity)
    try:
        resolved = server._ensure_identity(identity=None, meta=None)
    finally:
        _streamable_http_identity_ctx.reset(token)

    assert resolved is not None
    assert resolved.principal == "api-key-user"


# ---------------------------------------------------------------------------
# ContextVar propagation regression tests
#
# Root cause: StreamableHTTPSessionManager spawns a long-lived session task
# during `initialize`.  That task's context snapshot is taken *before* any
# per-request _streamable_http_identity_ctx is set in the middleware, so the
# identity ContextVar is always None inside handle_call_tool.
#
# Fix: _request_headers_from_context() reads Authorization from
# request_ctx.request.headers (set fresh per-request by the MCP SDK) and
# falls back to the _http_request_headers ContextVar.  _ensure_identity then
# re-authenticates from those live headers instead of the stale ContextVar.
# ---------------------------------------------------------------------------


def test_request_headers_from_context_reads_request_ctx_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary path: headers come from request_ctx.request.headers (per-request, always fresh)."""

    class _MockRequest:
        headers = {"authorization": "Bearer from-request-ctx", "x-api-key": "key123"}

    class _MockContext:
        request = _MockRequest()
        meta = None

    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:
        pytest.skip("mcp library not available")

    server = McpServer(connector_ids=["smtp"])
    tok = request_ctx.set(_MockContext())
    try:
        headers = server._request_headers_from_context()
    finally:
        request_ctx.reset(tok)

    assert headers is not None
    assert headers.get("authorization") == "Bearer from-request-ctx"
    assert headers.get("x-api-key") == "key123"


def test_request_headers_from_context_falls_back_to_http_request_headers_contextvar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback path: no request_ctx — reads from _http_request_headers ContextVar."""
    from bindings.mcp_server.server import _http_request_headers

    server = McpServer(connector_ids=["smtp"])
    tok = _http_request_headers.set({"authorization": "Bearer from-contextvar"})
    try:
        headers = server._request_headers_from_context()
    finally:
        _http_request_headers.reset(tok)

    assert headers is not None
    assert headers.get("authorization") == "Bearer from-contextvar"


def test_request_headers_from_context_returns_none_when_no_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When neither request_ctx nor ContextVar has headers, return None."""
    server = McpServer(connector_ids=["smtp"])
    # Neither ContextVar is set; request_ctx has no .request.headers
    result = server._request_headers_from_context()
    # Should be None (or an empty-headers context — either way, no Authorization)
    if result is not None:
        assert result.get("authorization") is None


@pytest.mark.asyncio
async def test_ensure_identity_falls_back_to_headers_when_identity_contextvar_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression: when _streamable_http_identity_ctx is NOT set (stale session task),
    _ensure_identity must re-authenticate from _http_request_headers and return
    the correct CallerIdentity including blocked_scopes from the JWT.
    """
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")

    token = jwt.encode(
        {
            "sub": "alice",
            "tenant_id": "demo",
            "scopes": ["mcp:smtp.send_email"],
            "blocked_scopes": ["mcp:smtp.send_email"],
        },
        "jwt-secret",
        algorithm="HS256",
    )

    from bindings.mcp_server.server import _http_request_headers, _streamable_http_identity_ctx

    server = McpServer(connector_ids=["smtp"])

    # Confirm identity ContextVar is NOT set (simulates stale session task)
    assert _streamable_http_identity_ctx.get() is None

    # Simulate _ASGIApp setting headers for this request
    hdr_tok = _http_request_headers.set({"authorization": f"Bearer {token}"})
    try:
        identity = server._ensure_identity(identity=None, meta=None)
    finally:
        _http_request_headers.reset(hdr_tok)

    assert identity is not None
    assert identity.principal == "alice"
    assert identity.tenant_id == "demo"
    assert "mcp:smtp.send_email" in identity.scopes
    assert "mcp:smtp.send_email" in identity.blocked_scopes


@pytest.mark.asyncio
async def test_blocked_scope_enforced_via_headers_when_identity_contextvar_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End-to-end regression test for the ContextVar propagation bug.

    Scenario: session task was created at initialize time — _streamable_http_identity_ctx
    is stale (None).  _http_request_headers carries the per-request Authorization header.
    invoke_tool must still enforce blocked_scopes and return POLICY_DENIED.
    """
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.delenv("NW_MCP_API_KEY", raising=False)
    monkeypatch.setenv("NW_MCP_JWT_SECRET", "jwt-secret")
    monkeypatch.setenv(
        "NW_MCP_ACTION_SCOPE_MAP_JSON",
        '{"smtp.send_email":"mcp:smtp.send_email"}',
    )

    # JWT: smtp.send_email is in *both* scopes and blocked_scopes.
    # Blocked always wins — tool must be denied.
    token = jwt.encode(
        {
            "sub": "restricted-caller",
            "tenant_id": "demo",
            "scopes": ["mcp:smtp.send_email"],
            "blocked_scopes": ["mcp:smtp.send_email"],
        },
        "jwt-secret",
        algorithm="HS256",
    )

    from bindings.mcp_server.server import _http_request_headers, _streamable_http_identity_ctx

    server = McpServer(connector_ids=["smtp"])

    # _streamable_http_identity_ctx must be absent (stale session task)
    assert _streamable_http_identity_ctx.get() is None

    # _http_request_headers carries the live per-request Authorization header
    hdr_tok = _http_request_headers.set({"authorization": f"Bearer {token}"})
    try:
        identity = server._ensure_identity(identity=None, meta=None)
        assert identity is not None, "Must resolve identity from headers even without ContextVar"

        resp = await server.invoke_tool(
            "smtp.send_email",
            {
                "from_email": "sender@example.com",
                "to": ["recipient@example.com"],
                "subject": "blocked scope test",
                "body": "this should never reach the SMTP server",
            },
            identity=identity,
        )
    finally:
        _http_request_headers.reset(hdr_tok)

    assert resp["success"] is False
    assert resp["error_code"] == "POLICY_DENIED"
    assert "Scope blocked for this action: mcp:smtp.send_email" in (resp["message"] or "")
