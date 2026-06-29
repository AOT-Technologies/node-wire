#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from bindings.rest_api.app import app, get_factory
from bindings.rest_api.body_limit import _path_requires_limit
from node_wire_runtime.models import ConnectorResponse


@pytest.fixture(autouse=True)
def _small_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_MAX_BODY_BYTES", "1024")


def _stub_connector() -> MagicMock:
    connector = MagicMock()
    connector.run = AsyncMock(
        return_value=ConnectorResponse(success=True, data={"ok": True}, trace_id="t-body")
    )
    return connector


def test_path_requires_limit_matches_connector_and_scenario_routes() -> None:
    assert _path_requires_limit({"type": "http", "method": "POST", "path": "/connectors/a/b"})
    assert _path_requires_limit({"type": "http", "method": "POST", "path": "/scenarios/foo"})
    assert not _path_requires_limit({"type": "http", "method": "GET", "path": "/connectors/a/b"})
    assert not _path_requires_limit({"type": "http", "method": "POST", "path": "/health"})
    assert not _path_requires_limit(
        {"type": "http", "method": "POST", "path": "/playground/index.html"}
    )


def test_rejects_oversized_content_length_on_connector_route() -> None:
    client = TestClient(app)
    response = client.post(
        "/connectors/http_generic/request",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": "2048",
        },
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_allows_request_under_limit() -> None:
    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector()
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        response = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code != 413
    assert response.status_code == 200


def test_skips_health_and_get() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_applies_to_scenarios_post() -> None:
    client = TestClient(app)
    response = client.post(
        "/scenarios/report-incident",
        content=b"x" * 2048,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_streaming_body_over_limit_without_content_length() -> None:
    client = TestClient(app)
    response = client.post(
        "/connectors/http_generic/request",
        content=b"x" * 2048,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
