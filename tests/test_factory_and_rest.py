from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from bindings.factory import ConnectorFactory
from bindings.rest_api.app import app, get_factory
from runtime.models import ConnectorResponse, ErrorCategory


def test_factory_loads_config():
    factory = ConnectorFactory()
    factory.load()

    http_connector = factory.get_for_protocol("http_generic", "rest")
    assert http_connector is not None

    stripe_rest = factory.get_for_protocol("stripe", "rest")
    assert stripe_rest is None  # stripe not exposed via REST per config


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def _stub_connector(response: ConnectorResponse) -> MagicMock:
    c = MagicMock()
    c.run = AsyncMock(return_value=response)
    return c


def test_rest_post_connector_success() -> None:
    """Dynamic POST forwards payload to connector.run and returns JSON with 200."""
    resp_body = ConnectorResponse(success=True, data={"ok": True}, trace_id="t-rest")
    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(resp_body)

    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post("/connectors/http_generic/request", json={"method": "GET", "url": "https://example.com"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["trace_id"] == "t-rest"
    mock_factory.get_for_protocol.assert_called_with("http_generic", "rest", action="request")
    stub = mock_factory.get_for_protocol.return_value
    stub.run.assert_awaited_once()
    call_payload = stub.run.await_args[0][0]
    assert call_payload["action"] == "request"
    assert call_payload["method"] == "GET"


@pytest.mark.parametrize(
    ("category", "expected_status"),
    [
        (ErrorCategory.BUSINESS, 400),
        (ErrorCategory.AUTH, 401),
        (ErrorCategory.RETRYABLE, 503),
        (ErrorCategory.FATAL, 500),
    ],
)
def test_rest_post_connector_error_category_http_status(
    category: ErrorCategory, expected_status: int
) -> None:
    resp_body = ConnectorResponse(
        success=False,
        trace_id="t-err",
        error_category=category,
        error_code="E1",
        message="nope",
    )
    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(resp_body)

    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post("/connectors/http_generic/request", json={"method": "GET", "url": "https://example.com"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == expected_status
    assert r.json()["success"] is False
    assert r.json()["error_category"] == category.value


def test_rest_post_connector_not_available_returns_404() -> None:
    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = None

    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post("/connectors/http_generic/request", json={})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 404
    assert r.json()["detail"] == "Connector not available for REST"


def test_http_status_for_category_direct() -> None:
    from bindings.rest_api.app import _http_status_for_category

    assert _http_status_for_category(None) == 200
    assert _http_status_for_category(ErrorCategory.BUSINESS) == 400
    assert _http_status_for_category(ErrorCategory.AUTH) == 401
    assert _http_status_for_category(ErrorCategory.RETRYABLE) == 503
    assert _http_status_for_category(ErrorCategory.FATAL) == 500

