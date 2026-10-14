#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Authorization tier on the /v1 config and secret admin API."""

from __future__ import annotations

import logging
from typing import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from bindings.factory import ConnectorFactory
from bindings.rest_api.app import app, get_factory
from tests.jwt_test_helpers import mint_test_jwt

_SECRET = "admin-authz-secret"
_INVOKE_ONLY = ["mcp:http_generic.request"]
_TENANT_ADMIN = ["nw:config:read", "nw:config:write", "nw:secrets:write"]


def _v1_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith("/v1")]


def _concrete(route: APIRoute) -> tuple[str, str]:
    method = sorted(route.methods)[0]
    return method, route.path.replace("{cid}", "http_generic").replace("{name}", "a")


def _headers(scopes: list[str], tenant: str | None = "acme", **extra: str) -> dict[str, str]:
    claims: dict = {"sub": "caller", "scopes": scopes}
    if tenant is not None:
        claims["tenant_id"] = tenant
    return {"Authorization": f"Bearer {mint_test_jwt(claims, _SECRET)}", **extra}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "false")
    monkeypatch.setenv("NW_REST_JWT_SECRET", _SECRET)
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = ConnectorFactory()
    for tenant in ("acme", "beta"):
        factory.store.create(tenant, "http_generic", {"name": "a", "default": True, "config": {}})
    app.dependency_overrides[get_factory] = lambda: factory
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_every_v1_route_declares_a_scope() -> None:
    routes = _v1_routes()
    assert routes
    undeclared = [
        r.path
        for r in routes
        if not any(getattr(d.call, "nw_scope", None) for d in r.dependant.dependencies)
    ]
    assert undeclared == []


@pytest.mark.parametrize("route", _v1_routes(), ids=lambda r: f"{sorted(r.methods)[0]} {r.path}")
def test_invoke_only_caller_is_refused(client: TestClient, route: APIRoute) -> None:
    method, path = _concrete(route)
    kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
    response = client.request(method, path, headers=_headers(_INVOKE_ONLY), **kwargs)
    assert response.status_code == 403, response.text


def test_tenant_admin_is_held_to_its_tenant(client: TestClient) -> None:
    own = client.get("/v1/connectors/http_generic/configs", headers=_headers(_TENANT_ADMIN))
    assert own.status_code == 200

    beta = _headers(_TENANT_ADMIN, **{"X-Tenant-ID": "beta"})
    read = client.get("/v1/connectors/http_generic/configs", headers=beta)
    assert read.status_code == 403
    write = client.put(
        "/v1/connectors/http_generic/configs/a", json={"name": "a", "config": {}}, headers=beta
    )
    assert write.status_code == 403
    secrets = client.put(
        "/v1/connectors/http_generic/secrets",
        json={"config_name": "a", "secrets": {"K": "v"}},
        headers=beta,
    )
    assert secrets.status_code == 403


def test_tenantless_caller_needs_platform_admin(client: TestClient) -> None:
    acme = {"X-Tenant-ID": "acme"}
    plain = _headers(_TENANT_ADMIN, tenant=None, **acme)
    assert client.get("/v1/connectors/http_generic/configs", headers=plain).status_code == 403
    admin = _headers(["nw:platform:admin"], tenant=None, **acme)
    assert client.get("/v1/connectors/http_generic/configs", headers=admin).status_code == 200
    tenants = client.get("/v1/tenants", headers=admin)
    assert tenants.status_code == 200
    assert set(tenants.json()["tenants"]) >= {"acme", "beta"}


def test_inline_secrets_need_secrets_write(client: TestClient) -> None:
    headers = _headers(["nw:config:write"])
    body = {"name": "b", "config": {}, "secrets": {"K": "v"}}
    refused = client.post("/v1/connectors/http_generic/configs", json=body, headers=headers)
    assert refused.status_code == 403
    body.pop("secrets")
    created = client.post("/v1/connectors/http_generic/configs", json=body, headers=headers)
    assert created.status_code == 201


def test_oversized_v1_body_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_MAX_BODY_BYTES", "64")
    response = client.post(
        "/v1/connectors/http_generic/configs",
        json={"name": "b", "config": {"pad": "x" * 128}},
        headers=_headers(_TENANT_ADMIN),
    )
    assert response.status_code == 413


def test_config_change_is_audited_without_values(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="bindings.rest_api"):
        missing = client.put(
            "/v1/connectors/http_generic/configs/missing",
            json={"name": "missing", "config": {}},
            headers=_headers(_TENANT_ADMIN),
        )
        assert missing.status_code == 404
        failed = [r for r in caplog.records if getattr(r, "audit_event", None) == "config_change"]
        assert failed == []
        client.put(
            "/v1/connectors/http_generic/configs/a",
            json={"name": "a", "config": {}, "secrets": {"API_TOKEN": "do-not-log"}},
            headers=_headers(_TENANT_ADMIN),
        )
    audits = [r for r in caplog.records if getattr(r, "audit_event", None) == "config_change"]
    assert audits
    line = audits[0].getMessage()
    assert "principal=caller" in line
    assert "tenant_id=acme" in line
    assert "API_TOKEN" in line
    assert "do-not-log" not in caplog.text
