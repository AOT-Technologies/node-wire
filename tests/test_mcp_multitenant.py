#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP binding multitenancy: session pin, stdio env pin, config_name (§6.3 / §6.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bindings.mcp_server.server import (
    McpServer,
    _http_request_headers,
    _session_tenant_ctx,
    _with_config_name_property,
)
from node_wire_runtime.models import ConnectorResponse


@pytest.fixture(autouse=True)
def _mcp_mt_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS",
        "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner",
    )
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY_SCOPES", raising=False)
    monkeypatch.delenv("NW_TENANT_ID", raising=False)
    monkeypatch.delenv("NW_MCP_ALLOWED_TENANTS", raising=False)
    monkeypatch.delenv("NW_MCP_TENANT_PIN_LOCKED", raising=False)
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")
    empty = tmp_path / "empty_tenants.yaml"
    empty.write_text("tenants: {}\n", encoding="utf-8")
    monkeypatch.setenv("NW_TENANTS_PATH", str(empty))


async def _capture_invoke(
    server: McpServer,
    *,
    tenant_for_instance: str,
    arguments: dict | None = None,
) -> dict[str, object]:
    """Provision tenant config, patch run(), invoke tool, return captured kwargs."""
    server._factory.store.create(
        tenant_for_instance,
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    connector = await server._factory.get(
        "http_generic", tenant_id=tenant_for_instance, config_name="default"
    )
    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["payload"] = dict(raw_input)
        captured["tenant_id"] = tenant_id
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    orig = connector.run
    connector.run = fake_run  # type: ignore[method-assign]
    try:
        await server.invoke_tool(
            "http_generic.request",
            arguments
            or {
                "method": "GET",
                "url": "https://example.com",
            },
        )
    finally:
        connector.run = orig  # type: ignore[method-assign]
    return captured


@pytest.mark.asyncio
async def test_mt_off_uses_default_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    server = McpServer(connector_ids=["http_generic"])
    tools = server.list_tools()
    assert all("config_name" not in (t["input_schema"].get("properties") or {}) for t in tools)

    connector = await server._factory.get("http_generic", tenant_id="__default__")
    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["tenant_id"] = tenant_id
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    orig = connector.run
    connector.run = fake_run  # type: ignore[method-assign]
    hdr_tok = _http_request_headers.set({"x-tenant-id": "acme"})
    try:
        await server.invoke_tool(
            "http_generic.request",
            {
                "method": "GET",
                "url": "https://example.com",
                "config_name": "should-be-ignored",
            },
        )
    finally:
        connector.run = orig  # type: ignore[method-assign]
        _http_request_headers.reset(hdr_tok)

    assert captured["tenant_id"] == "__default__"
    assert "config_name" not in captured["payload"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_mt_on_stdio_env_pin_selects_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")

    captured = await _capture_invoke(server, tenant_for_instance="acme")
    assert captured["tenant_id"] == "acme"
    assert "config_name" not in captured["payload"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_mt_on_session_pin_ignores_process_nw_tenant_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_TENANT_ID", "from-env-must-not-win")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("from-env-must-not-win")

    sess = _session_tenant_ctx.set("acme")
    try:
        captured = await _capture_invoke(server, tenant_for_instance="acme")
    finally:
        _session_tenant_ctx.reset(sess)

    assert captured["tenant_id"] == "acme"


@pytest.mark.asyncio
async def test_mt_on_no_pin_uses_default_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin(None)
    connector = await server._factory.get(
        "http_generic", tenant_id="__default__", config_name="default"
    )
    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["tenant_id"] = tenant_id
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    connector.run = fake_run  # type: ignore[method-assign]
    await server.invoke_tool(
        "http_generic.request",
        {"method": "GET", "url": "https://example.com"},
    )
    assert captured["tenant_id"] == "__default__"


@pytest.mark.asyncio
async def test_mt_on_select_tenant_overrides_stdio_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "other",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    await server.invoke_tool("nw_select_tenant", {"tenant_id": "other"})
    connector = await server._factory.get(
        "http_generic", tenant_id="other", config_name="default"
    )
    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["payload"] = dict(raw_input)
        captured["tenant_id"] = tenant_id
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    connector.run = fake_run  # type: ignore[method-assign]
    await server.invoke_tool(
        "http_generic.request",
        {"method": "GET", "url": "https://example.com"},
    )
    assert captured["tenant_id"] == "other"
    assert "tenant_id" not in captured["payload"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_mt_on_http_request_tenant_outranks_stale_session_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticket 1 of .scratch/mcp-tenant-config-per-request/: TenantSessionOverlay
    is one process-wide instance shared by every concurrent HTTP session. If
    one session calls nw_select_tenant, that must not shadow a *different*,
    concurrent HTTP request's own X-Tenant-ID — the live per-request signal
    (_session_tenant_ctx, set per-request by the auth middleware) must win.
    """
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    # One session pins the shared overlay to "acme" via nw_select_tenant...
    await server.invoke_tool("nw_select_tenant", {"tenant_id": "acme"})
    assert server.tenant_session.selected_tenant_id == "acme"

    # ...but *this* request's own resolved tenant is "other" (as a real HTTP
    # request would set via the auth middleware from X-Tenant-ID/JWT).
    sess = _session_tenant_ctx.set("other")
    try:
        captured = await _capture_invoke(server, tenant_for_instance="other")
    finally:
        _session_tenant_ctx.reset(sess)

    assert captured["tenant_id"] == "other"


@pytest.mark.asyncio
async def test_mt_on_tool_tenant_id_arg_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    captured = await _capture_invoke(
        server,
        tenant_for_instance="acme",
        arguments={
            "method": "GET",
            "url": "https://example.com",
            "tenant_id": "other",
        },
    )
    assert captured["tenant_id"] == "acme"
    assert "tenant_id" not in captured["payload"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_mt_on_pin_locked_rejects_select_not_tool_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_TENANT_PIN_LOCKED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "other",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )
    captured: dict[str, object] = {}
    connector = await server._factory.get(
        "http_generic", tenant_id="acme", config_name="default"
    )

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["tenant_id"] = tenant_id
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    connector.run = fake_run  # type: ignore[method-assign]
    await server.invoke_tool(
        "http_generic.request",
        {
            "method": "GET",
            "url": "https://example.com",
            "tenant_id": "other",
        },
    )
    assert captured["tenant_id"] == "acme"
    with pytest.raises(ValueError, match="NW_MCP_TENANT_PIN_LOCKED"):
        await server.invoke_tool("nw_select_tenant", {"tenant_id": "other"})


@pytest.mark.asyncio
async def test_mt_on_missing_tenant_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_ALLOWED_TENANTS", "acme")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin(None)

    with pytest.raises(ValueError, match="nw_select_tenant"):
        await server.invoke_tool(
            "http_generic.request",
            {"method": "GET", "url": "https://example.com"},
        )


@pytest.mark.asyncio
async def test_mt_on_unknown_config_via_select_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "primary", "default": True, "config": {}, "auth": {}},
    )

    connector = await server._factory.get("http_generic", tenant_id="acme", config_name="primary")
    captured: dict[str, object] = {}

    async def fake_run(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["payload"] = dict(raw_input)
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    connector.run = fake_run  # type: ignore[method-assign]
    await server.invoke_tool(
        "http_generic.request",
        {
            "method": "GET",
            "url": "https://example.com",
            "config_name": "primary",
        },
    )
    assert "config_name" not in captured["payload"]  # type: ignore[operator]

    with pytest.raises(ValueError, match="Unknown config"):
        await server.invoke_tool("nw_select_config", {"config_name": "does-not-exist"})


@pytest.mark.asyncio
async def test_mt_on_tool_config_name_arg_outranks_stale_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticket 2 of .scratch/mcp-tenant-config-per-request/: config_name was
    previously discarded from every connector-run tool call
    (`arguments.pop("config_name", None)`) and resolved purely from the
    shared, process-wide nw_select_config selection — with no per-request
    channel at all. An explicit per-call config_name argument must now
    outrank a stale session-selected config.
    """
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "prod-eu", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "prod-us", "default": False, "config": {}, "auth": {}},
    )
    # A stale session-selection points at "prod-eu"...
    await server.invoke_tool("nw_select_config", {"config_name": "prod-eu"})
    assert server.tenant_session.selected_config_name == "prod-eu"

    connector_eu = await server._factory.get(
        "http_generic", tenant_id="acme", config_name="prod-eu"
    )
    connector_us = await server._factory.get(
        "http_generic", tenant_id="acme", config_name="prod-us"
    )
    captured: dict[str, object] = {}

    async def fake_run_eu(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["config"] = "prod-eu"
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    async def fake_run_us(raw_input, *, principal=None, tenant_id=None, scopes=None):
        captured["config"] = "prod-us"
        return ConnectorResponse(success=True, data={"ok": True}, trace_id="t")

    connector_eu.run = fake_run_eu  # type: ignore[method-assign]
    connector_us.run = fake_run_us  # type: ignore[method-assign]

    # ...but *this* call explicitly asks for "prod-us".
    await server.invoke_tool(
        "http_generic.request",
        {"method": "GET", "url": "https://example.com", "config_name": "prod-us"},
    )
    assert captured["config"] == "prod-us"


@pytest.mark.asyncio
async def test_mt_on_connector_tools_advertise_config_but_not_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticket 3 of .scratch/mcp-tenant-config-per-request/: config_name is
    now a documented per-request argument (Ticket 2's channel); tenant_id
    stays header/JWT-only and is never advertised on connector-run tools."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    tools = server.list_tools()
    saw_connector_tool = False
    for t in tools:
        if t["name"].startswith("http_generic"):
            saw_connector_tool = True
            props = t["input_schema"].get("properties") or {}
            assert "config_name" in props
            assert "tenant_id" not in props
    assert saw_connector_tool


def test_with_config_name_property_adds_property():
    schema = {"type": "object", "properties": {"url": {"type": "string"}}}
    out = _with_config_name_property(schema)
    assert "config_name" in out["properties"]
    assert out["properties"]["url"] == {"type": "string"}
    assert "config_name" not in schema["properties"]  # original untouched


def test_with_config_name_property_skips_existing_field():
    schema = {
        "type": "object",
        "properties": {"config_name": {"type": "string", "description": "connector-owned"}},
    }
    out = _with_config_name_property(schema)
    assert out["properties"]["config_name"] == {
        "type": "string",
        "description": "connector-owned",
    }


def test_with_config_name_property_noop_without_properties():
    schema = {"type": "object"}
    assert _with_config_name_property(schema) is schema


@pytest.mark.asyncio
async def test_mt_off_connector_tools_omit_config_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    server = McpServer(connector_ids=["http_generic"])
    tools = server.list_tools()
    saw_connector_tool = False
    for t in tools:
        if t["name"].startswith("http_generic"):
            saw_connector_tool = True
            props = t["input_schema"].get("properties") or {}
            assert "config_name" not in props
    assert saw_connector_tool


@pytest.mark.asyncio
async def test_mt_on_null_config_name_uses_tenant_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMs emit config_name: null; treat as omit (tenant default)."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin("acme")
    captured = await _capture_invoke(
        server,
        tenant_for_instance="acme",
        arguments={
            "method": "GET",
            "url": "https://example.com",
            "config_name": None,
            "headers": None,
        },
    )
    assert captured["tenant_id"] == "acme"
    assert "config_name" not in captured["payload"]  # type: ignore[operator]
    assert "headers" not in captured["payload"]  # type: ignore[operator]


def test_stdio_pin_assignment_from_nw_tenant_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same assignment used at the start of _run_stdio_async."""
    monkeypatch.setenv("NW_TENANT_ID", "  acme  ")
    server = McpServer(connector_ids=["http_generic"])
    raw = os.getenv("NW_TENANT_ID")
    server.tenant_session.set_env_pin(raw.strip() if raw and raw.strip() else None)
    assert server.tenant_session.env_pin == "acme"


def test_streamable_http_mt_missing_header_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient
    from starlette.responses import JSONResponse

    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")

    class _FakeStreamableSessionManager:
        async def handle_request(self, scope, receive, send):
            response = JSONResponse({"ok": True})
            await response(scope, receive, send)

    server = McpServer(connector_ids=["http_generic"])
    app = server._build_streamable_http_app(
        session_manager=_FakeStreamableSessionManager(),
        path="/mcp",
    )
    client = TestClient(app)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": "1", "method": "tools/list"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
