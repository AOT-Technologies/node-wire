# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Drive a generated node-wire-mode server over streamable HTTP, in-process.

ToolHive's role is played by the test: it sends ``Authorization: Bearer`` and ``X-Tenant-ID``.
The upstream API is an ``httpx.MockTransport``.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

import node_wire_runtime.rest as rest_module
from mcp_builder.pipeline import run_pipeline
from nw_stacklok.hooks import NodeWireOptions

from .conftest import CONNECTOR_ID, PETSTORE_SPEC, TEMPLATE, NodeWireCheckout

_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> List[httpx.Request]:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": 7, "name": "doggie", "status": "available"})

    real = httpx.AsyncClient

    class _Client(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    async def _no_ssrf_check(url: str) -> None:
        return None

    monkeypatch.setattr(rest_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(rest_module, "assert_safe_destination", _no_ssrf_check)
    return seen


@pytest.fixture
def server(
    node_wire_checkout: NodeWireCheckout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    out = tmp_path / "out"
    out.mkdir()
    project = run_pipeline(
        node_wire_checkout.scope(tmp_path),
        PETSTORE_SPEC,
        TEMPLATE,
        out,
        node_wire=NodeWireOptions(node_wire_checkout.root, wheels=False),
    )
    example = yaml.safe_load((project / "config" / "tenants.example.yaml").read_text())
    doc = example["tenants"]["example-tenant"][CONNECTOR_ID][0]
    tenants = {
        "tenants": {
            "acme": {CONNECTOR_ID: [{**doc, "base_url": "https://acme.example.test"}]},
            "globex": {CONNECTOR_ID: [{**doc, "base_url": "https://globex.example.test"}]},
        }
    }
    tenants_file = tmp_path / "tenants.yaml"
    tenants_file.write_text(yaml.safe_dump(tenants), encoding="utf-8")
    for key, value in {
        "NW_CONFIG_PATH": str(project / "config" / "connectors.yaml"),
        "NW_TENANTS_PATH": str(tenants_file),
        "NW_MULTITENANCY_ENABLED": "true",
        "NW_MCP_SCOPE_POLICY_DEFAULT": "allow",
        "NW_ALLOWED_CONNECTORS": CONNECTOR_ID,
        "NW_UPSTREAM_BEARER_CONNECTORS": CONNECTOR_ID,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)  # the template's Settings reads ./.env
    monkeypatch.syspath_prepend(str(project / "src"))
    for name in [m for m in sys.modules if m == "petstore_mcp" or m.startswith("petstore_mcp.")]:
        del sys.modules[name]
    app_builder = importlib.import_module("petstore_mcp.api.app_builder")
    with TestClient(app_builder.AppBuilder.build_app()) as client:
        yield client


class _Session:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.session_id: str | None = None
        self._id = 0

    def rpc(self, method: str, params: Dict[str, Any], *, headers: Dict[str, str]) -> Any:
        self._id += 1
        h = {**_ACCEPT, **headers}
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        response = self.client.post("/mcp", headers=h, content=json.dumps(body))
        assert response.status_code == 200, response.text
        self.session_id = self.session_id or response.headers.get("mcp-session-id")
        data = [line for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1][5:] if data else response.text)

    def open(self, headers: Dict[str, str]) -> None:
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
            headers=headers,
        )
        h = {**_ACCEPT, **headers, "mcp-session-id": self.session_id or ""}
        self.client.post(
            "/mcp",
            headers=h,
            content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        )

    def call(self, tool: str, arguments: Dict[str, Any], *, headers: Dict[str, str]) -> Any:
        return self.rpc("tools/call", {"name": tool, "arguments": arguments}, headers=headers)[
            "result"
        ]


def _as(tenant: str | None, token: str) -> Dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-Tenant-ID"] = tenant
    return headers


def test_lists_the_scoped_tools_and_config_tools(server: TestClient) -> None:
    session = _Session(server)
    session.open(_as("acme", "tok"))
    tools = session.rpc("tools/list", {}, headers=_as("acme", "tok"))["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "find_pets_by_status",
        "get_pet_by_id",
        "place_order",
        "get_order_by_id",
        "get_user_by_name",
        "nw_list_configs",
        "nw_select_config",
    }


def test_calls_reach_the_tenants_api_with_the_forwarded_credential(
    server: TestClient, upstream: List[httpx.Request]
) -> None:
    session = _Session(server)
    session.open(_as("acme", "tok-1"))

    result = session.call("get_pet_by_id", {"petId": 7}, headers=_as("acme", "tok-1"))
    assert result["isError"] is False, result
    # A refreshed credential later in the same MCP session is the one relayed.
    session.call("find_pets_by_status", {"status": "sold"}, headers=_as("acme", "tok-2"))

    first, second = upstream
    assert str(first.url) == "https://acme.example.test/pet/7"
    assert first.headers["api_key"] == "tok-1"  # connector default scheme (api_key header)
    assert second.url.path == "/pet/findByStatus"
    assert second.url.params["status"] == "sold"
    assert second.headers["authorization"] == "Bearer tok-2"  # petstore_auth scheme


def test_tenants_are_isolated_by_header(server: TestClient, upstream: List[httpx.Request]) -> None:
    for tenant in ("acme", "globex"):
        session = _Session(server)
        session.open(_as(tenant, "tok"))
        session.call("get_pet_by_id", {"petId": 7}, headers=_as(tenant, "tok"))
    assert [r.url.host for r in upstream] == ["acme.example.test", "globex.example.test"]


def test_no_tenant_header_is_a_tool_error(
    server: TestClient, upstream: List[httpx.Request]
) -> None:
    session = _Session(server)
    session.open(_as(None, "tok"))
    result = session.call("get_pet_by_id", {"petId": 7}, headers=_as(None, "tok"))
    assert result["isError"] is True
    assert "TENANT_REQUIRED" in result["content"][0]["text"]
    assert upstream == []


def test_requests_without_a_bearer_token_are_rejected(server: TestClient) -> None:
    response = server.post("/mcp", headers={**_ACCEPT, "X-Tenant-ID": "acme"}, content="{}")
    assert response.status_code == 401
