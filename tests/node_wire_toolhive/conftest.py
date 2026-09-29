# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for node-wire-toolhive: a tiny REST connector, tenants, fake HTTP upstream."""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Dict, Iterator, List, Literal, Mapping, Type

import httpx
import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field

import node_wire_runtime.rest as rest_module
from mcp.server.lowlevel.server import request_ctx
from node_wire_runtime import RestConnector, RestResponseOutput, nw_action

CONNECTOR_ID = "th_demo"


class GetPetInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    action: Literal["get_pet"] = "get_pet"
    petid: str = Field(
        ..., alias="petId", json_schema_extra={"nw_in": "path", "nw_wire_name": "petId"}
    )


class ThDemoConnector(RestConnector):
    connector_id = CONNECTOR_ID
    default_base_url = "https://baked.example.test"
    output_model: ClassVar[Type[BaseModel]] = RestResponseOutput
    _nw_abstract_base = False

    @nw_action("get_pet")
    async def get_pet(self, params: GetPetInput, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            method="GET",
            path_template="/pets/{petId}",
            params=params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
        )


def _doc(
    name: str, base_url: str, auth: Dict[str, Any], *, default: bool = False
) -> Dict[str, Any]:
    return {
        "name": name,
        "default": default,
        "base_url": base_url,
        "auth": auth,
        "exposed_via": ["mcp"],
    }


TENANTS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "acme": {
        CONNECTOR_ID: [
            _doc(
                "default",
                "https://acme.example.test",
                {"provider": "static_token", "secret_key": "T"},
                default=True,
            ),
            _doc(
                "eu",
                "https://acme-eu.example.test",
                {"provider": "static_token", "secret_key": "T"},
            ),
        ]
    },
    "globex": {
        CONNECTOR_ID: [
            _doc(
                "default",
                "https://globex.example.test",
                {
                    "provider": "static_token",
                    "secret_key": "K",
                    "header_name": "X-API-Key",
                    "prefix": "",
                },
                default=True,
            )
        ]
    },
    "initech": {
        CONNECTOR_ID: [
            _doc(
                "default",
                "https://initech.example.test",
                {"provider": "apikey_query", "name": "api_key", "secret_key": "Q"},
                default=True,
            )
        ]
    },
}


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> List[httpx.Request]:
    """Capture every outbound connector request; answer 200 JSON."""
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "p1"})

    real_client = httpx.AsyncClient

    class _Client(real_client):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    async def _no_ssrf_check(url: str) -> None:
        return None

    monkeypatch.setattr(rest_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(rest_module, "assert_safe_destination", _no_ssrf_check)
    return seen


@pytest.fixture
def node_wire_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """connectors.yaml + tenants.yaml for th_demo; multitenancy on; relay opted in."""
    config = tmp_path / "connectors.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "connectors": {
                    CONNECTOR_ID: {
                        "enabled": True,
                        "exposed_via": ["mcp"],
                        "base_url": "https://default.example.test",
                        "auth": {"provider": "static_token", "secret_key": "T"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    tenants = tmp_path / "tenants.yaml"
    tenants.write_text(yaml.safe_dump({"tenants": TENANTS}), encoding="utf-8")
    monkeypatch.setenv("NW_TENANTS_PATH", str(tenants))
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_UPSTREAM_BEARER_CONNECTORS", CONNECTOR_ID)
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", CONNECTOR_ID)
    return config


@contextlib.contextmanager
def mcp_request(headers: Mapping[str, str], *, session: object | None = None) -> Iterator[None]:
    """Make ``headers`` the HTTP request behind the MCP message being handled."""
    ctx = SimpleNamespace(
        request=SimpleNamespace(headers=httpx.Headers(dict(headers))),
        session=session if session is not None else object(),
    )
    token = request_ctx.set(ctx)  # type: ignore[arg-type]
    try:
        yield
    finally:
        request_ctx.reset(token)
