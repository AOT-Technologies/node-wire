#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""MCP nw_list_configs / nw_list_tenants meta-tools and tenants.yaml hydrate."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bindings.mcp_server.server import (
    LIST_CONFIGS_TOOL,
    LIST_TENANTS_TOOL,
    SELECT_CONFIG_TOOL,
    SELECT_TENANT_TOOL,
    McpServer,
    format_list_tenants_text,
)


def _tenant_ids_from_markdown(text: str) -> list[str]:
    ids: list[str] = []
    for line in text.splitlines():
        if not line.startswith("- `"):
            continue
        ids.append(line[3:].split("`", 1)[0])
    return ids


@pytest.fixture(autouse=True)
def _mcp_list_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
    # Avoid loading the developer's real tenants.yaml into every McpServer().
    empty = tmp_path / "empty_tenants.yaml"
    empty.write_text("tenants: {}\n", encoding="utf-8")
    monkeypatch.setenv("NW_TENANTS_PATH", str(empty))


@pytest.mark.asyncio
async def test_list_configs_hidden_when_mt_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    server = McpServer(connector_ids=["http_generic"])
    names = {t["name"] for t in server.list_tools()}
    assert LIST_CONFIGS_TOOL not in names
    assert LIST_TENANTS_TOOL not in names
    assert SELECT_TENANT_TOOL not in names
    assert SELECT_CONFIG_TOOL not in names

    with pytest.raises(ValueError, match="NW_MULTITENANCY_ENABLED"):
        await server.invoke_tool(LIST_CONFIGS_TOOL, {})
    with pytest.raises(ValueError, match="NW_MULTITENANCY_ENABLED"):
        await server.invoke_tool(LIST_TENANTS_TOOL, {})
    with pytest.raises(ValueError, match="NW_MULTITENANCY_ENABLED"):
        await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme"})
    with pytest.raises(ValueError, match="NW_MULTITENANCY_ENABLED"):
        await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "x"})


@pytest.mark.asyncio
async def test_list_configs_returns_named_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic", "google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "test", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "test-new", "default": False, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )

    tools = server.list_tools()
    assert any(t["name"] == LIST_CONFIGS_TOOL for t in tools)

    all_cfgs = await server.invoke_tool(LIST_CONFIGS_TOOL, {})
    assert all_cfgs["ok"] is True
    assert all_cfgs["tenant_id"] == "acme"
    names = {(c["connector_id"], c["name"]) for c in all_cfgs["configs"]}
    assert ("google_drive", "test") in names
    assert ("google_drive", "test-new") in names
    assert ("http_generic", "default") in names

    drive_only = await server.invoke_tool(LIST_CONFIGS_TOOL, {"connector_id": "google_drive"})
    drive_names = {c["name"] for c in drive_only["configs"]}
    assert drive_names == {"test", "test-new"}
    assert all(c["connector_id"] == "google_drive" for c in drive_only["configs"])


@pytest.mark.asyncio
async def test_list_configs_respects_connector_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "drive-a", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "http-a", "default": True, "config": {}, "auth": {}},
    )

    result = await server.invoke_tool(LIST_CONFIGS_TOOL, {})
    assert {c["name"] for c in result["configs"]} == {"drive-a"}

    with pytest.raises(ValueError, match="not allowed"):
        await server.invoke_tool(LIST_CONFIGS_TOOL, {"connector_id": "http_generic"})


@pytest.mark.asyncio
async def test_list_configs_without_pin_uses_default_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic"])
    server.tenant_session.set_env_pin(None)
    result = await server.invoke_tool(LIST_CONFIGS_TOOL, {})
    assert result["ok"] is True
    assert result["tenant_id"] == "__default__"


def test_mcp_server_hydrates_tenants_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    yaml_path = tmp_path / "tenants.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "tenants": {
                    "acme": {
                        "http_generic": [
                            {
                                "name": "from-yaml",
                                "default": True,
                                "config": {},
                                "auth": {},
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NW_TENANTS_PATH", str(yaml_path))

    server = McpServer(connector_ids=["http_generic"])
    docs = server._factory.store.list("acme", "http_generic")
    assert any(d.get("name") == "from-yaml" for d in docs)
    assert all("connector_id" in d for d in docs)


@pytest.mark.asyncio
async def test_list_tenants_returns_ids_filtered_by_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic", "google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "test", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-test",
        "google_drive",
        {"name": "test-demo", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "http-only",
        "http_generic",
        {"name": "default", "default": True, "config": {}, "auth": {}},
    )

    tools = server.list_tools()
    assert any(t["name"] == LIST_TENANTS_TOOL for t in tools)

    all_tenants = await server.invoke_tool(LIST_TENANTS_TOOL, {})
    assert all_tenants["ok"] is True
    assert all_tenants["pinned_tenant_id"] == "acme"
    assert all_tenants["current_tenant_id"] == "acme"
    assert "current: `acme`" in all_tenants["summary"]
    assert "- `acme`  *(current)*" in all_tenants["summary"]
    names = set(all_tenants["tenants"])
    assert {"acme", "acme-test", "http-only"} <= names
    assert _tenant_ids_from_markdown(all_tenants["summary"]) == all_tenants["tenants"]

    drive = await server.invoke_tool(LIST_TENANTS_TOOL, {"connector_id": "google_drive"})
    assert drive["connector_id"] == "google_drive"
    assert "Tenants with `google_drive` configs" in drive["summary"]
    drive_names = set(drive["tenants"])
    assert {"acme", "acme-test"} <= drive_names
    assert "http-only" not in drive_names

    aliased = await server.invoke_tool("nw.list_tenants", {"connector_id": "google_drive"})
    assert aliased == drive


@pytest.mark.asyncio
async def test_list_tenants_works_without_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin(None)
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "test", "default": True, "config": {}, "auth": {}},
    )

    result = await server.invoke_tool(LIST_TENANTS_TOOL, {})
    assert result["ok"] is True
    assert result["pinned_tenant_id"] == "__default__"
    assert "acme" in result["tenants"]
    assert "current: `__default__`" in result["summary"]


@pytest.mark.asyncio
async def test_list_tenants_respects_connector_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "drive-a", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "other",
        "http_generic",
        {"name": "http-a", "default": True, "config": {}, "auth": {}},
    )

    result = await server.invoke_tool(LIST_TENANTS_TOOL, {})
    assert "acme" in result["tenants"]
    assert "other" not in result["tenants"]

    with pytest.raises(ValueError, match="not allowed"):
        await server.invoke_tool(LIST_TENANTS_TOOL, {"connector_id": "http_generic"})


def test_format_list_tenants_text() -> None:
    text = format_list_tenants_text(
        tenants=["acme", "acme-test"],
        connector_id="google_drive",
        pinned_tenant_id="acme",
    )
    assert text.startswith("Tenants with `google_drive` configs (current: `acme`)")
    assert "- `acme`  *(current)*" in text
    assert "- `acme-test`" in text
    assert "{" not in text
    assert "nw_select_tenant" in text

    empty = format_list_tenants_text(tenants=[], connector_id=None, pinned_tenant_id=None)
    assert "No tenants found." in empty
    assert "no tenant selected" in empty


@pytest.mark.asyncio
async def test_select_tenant_overrides_pin_and_returns_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "pin-cfg", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "google_drive",
        {"name": "test-work", "default": True, "config": {}, "auth": {}},
    )

    selected = await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme-demo"})
    assert selected["ok"] is True
    assert selected["tenant_id"] == "acme-demo"
    assert {c["name"] for c in selected["configs"]} == {"test-work"}

    listed = await server.invoke_tool(LIST_CONFIGS_TOOL, {})
    assert listed["tenant_id"] == "acme-demo"
    assert {c["name"] for c in listed["configs"]} == {"test-work"}

    aliased = await server.invoke_tool("nw.select_tenant", {"tenant_id": "acme"})
    assert aliased["tenant_id"] == "acme"


@pytest.mark.asyncio
async def test_list_configs_honors_tenant_id_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "pin-cfg", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "google_drive",
        {"name": "other", "default": True, "config": {}, "auth": {}},
    )
    result = await server.invoke_tool(
        LIST_CONFIGS_TOOL, {"tenant_id": "acme-demo", "connector_id": "google_drive"}
    )
    assert result["tenant_id"] == "acme-demo"
    assert {c["name"] for c in result["configs"]} == {"other"}


@pytest.mark.asyncio
async def test_select_unknown_tenant_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    with pytest.raises(ValueError, match="Unknown tenant"):
        await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "nope"})


@pytest.mark.asyncio
async def test_select_tenant_respects_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_ALLOWED_TENANTS", "acme")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "a", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "google_drive",
        {"name": "b", "default": True, "config": {}, "auth": {}},
    )
    listed = await server.invoke_tool(LIST_TENANTS_TOOL, {})
    assert listed["tenants"] == ["acme"]
    with pytest.raises(ValueError, match="not allowed"):
        await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme-demo"})


@pytest.mark.asyncio
async def test_select_tenant_pin_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_TENANT_PIN_LOCKED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "a", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "google_drive",
        {"name": "b", "default": True, "config": {}, "auth": {}},
    )
    with pytest.raises(ValueError, match="NW_MCP_TENANT_PIN_LOCKED"):
        await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme-demo"})
    with pytest.raises(ValueError, match="NW_MCP_TENANT_PIN_LOCKED"):
        await server.invoke_tool(LIST_CONFIGS_TOOL, {"tenant_id": "acme-demo"})


@pytest.mark.asyncio
async def test_select_tenant_clears_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "pin-cfg", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "google_drive",
        {"name": "other", "default": True, "config": {}, "auth": {}},
    )
    picked = await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "pin-cfg"})
    assert picked["config_name"] == "pin-cfg"
    switched = await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme-demo"})
    assert switched["selected_config_name"] is None


@pytest.mark.asyncio
async def test_select_config_sets_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "pin-cfg", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "alt", "default": False, "config": {}, "auth": {}},
    )
    result = await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "alt"})
    assert result["ok"] is True
    assert result["config_name"] == "alt"
    assert result["connectors_with_config"] == ["google_drive"]
    assert result["connectors_missing_config"] == []
    assert server.tenant_session.selected_config_name == "alt"


@pytest.mark.asyncio
async def test_select_config_applies_to_every_connector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic", "google_drive"])
    server.tenant_session.set_env_pin("acme")
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "shared", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "shared", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme",
        "google_drive",
        {"name": "drive-only", "default": False, "config": {}, "auth": {}},
    )

    shared = await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "shared"})
    assert shared["connectors_with_config"] == ["google_drive", "http_generic"]
    assert shared["connectors_missing_config"] == []

    drive_only = await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "drive-only"})
    assert drive_only["connectors_with_config"] == ["google_drive"]
    assert drive_only["connectors_missing_config"] == ["http_generic"]

    with pytest.raises(ValueError, match="not defined for connector 'http_generic'"):
        await server.invoke_tool(
            "http_generic.request",
            {"method": "GET", "url": "https://example.com"},
        )


@pytest.mark.asyncio
async def test_select_tenant_keeps_config_if_any_connector_has_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    server = McpServer(connector_ids=["http_generic", "google_drive"])
    server.tenant_session.set_env_pin("acme")
    for tenant in ("acme", "acme-demo"):
        server._factory.store.create(
            tenant,
            "google_drive",
            {"name": "shared", "default": True, "config": {}, "auth": {}},
        )
    server._factory.store.create(
        "acme",
        "http_generic",
        {"name": "shared", "default": True, "config": {}, "auth": {}},
    )
    server._factory.store.create(
        "acme-demo",
        "http_generic",
        {"name": "other", "default": True, "config": {}, "auth": {}},
    )
    await server.invoke_tool(SELECT_CONFIG_TOOL, {"config_name": "shared"})
    switched = await server.invoke_tool(SELECT_TENANT_TOOL, {"tenant_id": "acme-demo"})
    assert switched["selected_config_name"] == "shared"
    assert switched["connectors_with_config"] == ["google_drive"]
    assert switched["connectors_missing_config"] == ["http_generic"]
    with pytest.raises(ValueError, match="not defined for connector 'http_generic'"):
        await server.invoke_tool(
            "http_generic.request",
            {"method": "GET", "url": "https://example.com"},
        )
