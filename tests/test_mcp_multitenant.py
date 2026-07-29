#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP binding multitenancy: session pin, stdio env pin, config_name (§6.3 / §6.4)."""

from __future__ import annotations

import os

import pytest

from bindings.mcp_server.server import (
    McpServer,
    _http_request_headers,
    _session_tenant_ctx,
)
from node_wire_runtime.models import ConnectorResponse


@pytest.fixture(autouse=True)
def _mcp_mt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS",
        "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner",
    )
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    monkeypatch.delenv("NW_MCP_ACTION_SCOPE_MAP_JSON", raising=False)
    monkeypatch.delenv("NW_MCP_API_KEY_SCOPES", raising=False)
    monkeypatch.delenv("NW_TENANT_ID", raising=False)
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")


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
    server._stdio_env_tenant_pin = "acme"

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
    server._stdio_env_tenant_pin = "from-env-must-not-win"

    sess = _session_tenant_ctx.set("acme")
    try:
        captured = await _capture_invoke(server, tenant_for_instance="acme")
    finally:
        _session_tenant_ctx.reset(sess)

    assert captured["tenant_id"] == "acme"


@pytest.mark.asyncio
async def test_mt_on_missing_tenant_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server._stdio_env_tenant_pin = None

    with pytest.raises(ValueError, match="X-Tenant-ID is required"):
        await server.invoke_tool(
            "http_generic.request",
            {"method": "GET", "url": "https://example.com"},
        )


@pytest.mark.asyncio
async def test_mt_on_config_name_stripped_and_unknown_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server._stdio_env_tenant_pin = "acme"
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

    with pytest.raises(ValueError, match="not available via MCP"):
        await server.invoke_tool(
            "http_generic.request",
            {
                "method": "GET",
                "url": "https://example.com",
                "config_name": "does-not-exist",
            },
        )


@pytest.mark.asyncio
async def test_mt_on_config_name_in_tool_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    tools = server.list_tools()
    cfg_schemas = [
        (t["input_schema"].get("properties") or {}).get("config_name")
        for t in tools
        if "config_name" in (t["input_schema"].get("properties") or {})
    ]
    assert cfg_schemas
    assert cfg_schemas[0]["type"] == ["string", "null"]


@pytest.mark.asyncio
async def test_mt_on_null_config_name_uses_tenant_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLMs emit config_name: null; treat as omit (tenant default)."""
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server._stdio_env_tenant_pin = "acme"
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
    server._stdio_env_tenant_pin = raw.strip() if raw and raw.strip() else None
    assert server._stdio_env_tenant_pin == "acme"
