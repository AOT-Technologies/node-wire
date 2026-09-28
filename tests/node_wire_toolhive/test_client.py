# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""NodeWireClient: tenant from the ToolHive header, credential relay, per-session configs."""

from __future__ import annotations

from pathlib import Path
from typing import List

import httpx
import pytest

from node_wire_toolhive import NodeWireClient, NodeWireToolError

from .conftest import CONNECTOR_ID, mcp_request


def _headers(tenant: str | None, token: str | None, session: str = "s1") -> dict[str, str]:
    headers = {"mcp-session-id": session}
    if tenant is not None:
        headers["x-tenant-id"] = tenant
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return headers


@pytest.mark.parametrize(
    ("tenant", "check"),
    [
        ("acme", lambda r: r.headers["authorization"] == "Bearer tok-1"),
        (
            "globex",
            lambda r: r.headers["x-api-key"] == "tok-1" and "authorization" not in r.headers,
        ),
        (
            "initech",
            lambda r: r.url.params["api_key"] == "tok-1" and "authorization" not in r.headers,
        ),
    ],
)
async def test_credential_lands_where_the_tenants_auth_block_says(
    node_wire_env: Path, upstream: List[httpx.Request], tenant: str, check
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers(tenant, "tok-1")):
        data = await client.run("get_pet", {"petid": "p1"})

    assert data is not None
    request = upstream[-1]
    assert request.url.host == f"{tenant}.example.test"
    assert request.url.path == "/pets/p1"
    assert check(request)


@pytest.mark.parametrize("tenant", ["acme", "globex", "initech"])
async def test_each_call_uses_its_own_requests_credential(
    node_wire_env: Path, upstream: List[httpx.Request], tenant: str
) -> None:
    """The factory caches one connector per (tenant, config); the credential must not be."""
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    for token in ("first", "second", "third"):
        with mcp_request(_headers(tenant, token)):
            await client.run("get_pet", {"petid": "p1"})

    sent = [
        r.headers.get("authorization") or r.headers.get("x-api-key") or r.url.params.get("api_key")
        for r in upstream
    ]
    assert [s.removeprefix("Bearer ") for s in sent] == ["first", "second", "third"]


async def test_missing_credential_is_a_tool_error(
    node_wire_env: Path, upstream: List[httpx.Request]
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers("acme", None)), pytest.raises(NodeWireToolError):
        await client.run("get_pet", {"petid": "p1"})
    assert upstream == []


async def test_missing_tenant_header_is_a_tool_error(
    node_wire_env: Path, upstream: List[httpx.Request]
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers(None, "tok")), pytest.raises(NodeWireToolError) as excinfo:
        await client.run("get_pet", {"petid": "p1"})
    assert excinfo.value.error_code == "TENANT_REQUIRED"
    assert upstream == []


async def test_unknown_tenant_is_refused(
    node_wire_env: Path, upstream: List[httpx.Request]
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers("umbrella", "tok")), pytest.raises(NodeWireToolError):
        await client.run("get_pet", {"petid": "p1"})
    assert upstream == []


async def test_selected_config_is_per_session(
    node_wire_env: Path, upstream: List[httpx.Request]
) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers("acme", "tok", session="eu-session")):
        assert client.select_config("eu") == "acme"
        await client.run("get_pet", {"petid": "p1"})
    with mcp_request(_headers("acme", "tok", session="other-session")):
        await client.run("get_pet", {"petid": "p1"})

    assert [r.url.host for r in upstream] == ["acme-eu.example.test", "acme.example.test"]


async def test_selecting_an_unknown_config_fails(node_wire_env: Path) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers("acme", "tok")), pytest.raises(NodeWireToolError) as excinfo:
        client.select_config("apac")
    assert excinfo.value.error_code == "CONFIG_NOT_FOUND"


async def test_relay_requires_the_explicit_opt_in(
    node_wire_env: Path, upstream: List[httpx.Request], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NW_UPSTREAM_BEARER_CONNECTORS", "someone_else")
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    with mcp_request(_headers("acme", "tok")), pytest.raises(NodeWireToolError) as excinfo:
        await client.run("get_pet", {"petid": "p1"})
    assert "NW_UPSTREAM_BEARER_CONNECTORS" in str(excinfo.value)
    assert upstream == []


async def test_config_names_lists_the_tenants_configs(node_wire_env: Path) -> None:
    client = NodeWireClient(CONNECTOR_ID, config_path=node_wire_env)
    assert client.config_names("acme") == [
        {"name": "default", "default": True},
        {"name": "eu", "default": False},
    ]
