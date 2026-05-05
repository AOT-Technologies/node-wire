from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from bindings.factory import ConnectorFactory
from bindings.rest_api.app import app, get_factory
from node_wire_runtime.models import ConnectorResponse, ErrorCategory


def test_factory_loads_config():
    factory = ConnectorFactory()
    factory.load()

    http_connector = factory.get_for_protocol("http_generic", "rest")
    assert http_connector is not None

    stripe_rest = factory.get_for_protocol("stripe", "rest")
    assert stripe_rest is not None  # stripe exposed via REST


def test_health_endpoint():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_agent_transport_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NW_MCP_TRANSPORT", raising=False)
    client = TestClient(app)
    resp = client.get("/scenarios/agent-transport")
    assert resp.status_code == 200
    assert resp.json() == {"transport": "stdio", "label": "stdio"}


def test_agent_transport_reports_streamable_http(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NW_MCP_TRANSPORT", "streamable-http")
    client = TestClient(app)
    resp = client.get("/scenarios/agent-transport")
    assert resp.status_code == 200
    assert resp.json() == {
        "transport": "streamable-http",
        "label": "Streamable HTTP",
    }


def test_rest_post_without_auth_returns_401_when_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_REST_JWT_SECRET", raising=False)
    monkeypatch.setenv("NW_REST_API_KEY", "unit-test-secret")

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(
        ConnectorResponse(success=True, data={}, trace_id="t")
    )
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post("/connectors/http_generic/request", json={"method": "GET", "url": "https://example.com"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401
    assert "Authentication" in r.json()["detail"]


def test_rest_post_with_bearer_succeeds_when_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_REST_API_KEY", "unit-test-secret")

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(
        ConnectorResponse(success=True, data={"ok": True}, trace_id="t-rest")
    )
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"Authorization": "Bearer unit-test-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200


def test_rest_post_propagates_api_key_identity_to_connector_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_REST_JWT_SECRET", raising=False)
    monkeypatch.setenv("NW_REST_API_KEY", "unit-test-secret")

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(
        ConnectorResponse(success=True, data={}, trace_id="t-p")
    )
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"Authorization": "Bearer unit-test-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    stub = mock_factory.get_for_protocol.return_value
    kwargs = stub.run.await_args.kwargs
    assert kwargs["principal"] == "api-key-user"
    assert kwargs["tenant_id"] is None
    assert kwargs["scopes"] == ("*",)


def test_rest_post_propagates_jwt_claims_to_connector_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_REST_API_KEY", raising=False)
    secret = "rest-jwt-test-secret-at-least-32bytes!!"
    monkeypatch.setenv("NW_REST_JWT_SECRET", secret)

    tok = jwt.encode(
        {"sub": "alice", "tenant_id": "t-1", "scopes": ["mcp:test.scope"]},
        secret,
        algorithm="HS256",
    )

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(
        ConnectorResponse(success=True, data={}, trace_id="t-j")
    )
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    finally:
        app.dependency_overrides.clear()

    stub = mock_factory.get_for_protocol.return_value
    kw = stub.run.await_args.kwargs
    assert kw["principal"] == "alice"
    assert kw["tenant_id"] == "t-1"
    assert kw["scopes"] == ("mcp:test.scope",)


def test_rest_not_configured_returns_503_when_no_key_and_not_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("NW_REST_API_KEY", raising=False)
    monkeypatch.delenv("NW_REST_JWT_SECRET", raising=False)

    client = TestClient(app)
    r = client.post("/connectors/http_generic/request", json={})
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


def test_health_public_when_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("NW_REST_API_KEY", "unit-test-secret")

    client = TestClient(app)
    assert client.get("/health").status_code == 200


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


def test_rest_post_connector_rejects_conflicting_action_in_body() -> None:
    """Body action must match URL path segment (same as MCP tool name authority)."""
    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector(
        ConnectorResponse(success=True, data={}, trace_id="t")
    )

    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        r = client.post(
            "/connectors/http_generic/request",
            json={"action": "wrong_action", "method": "GET", "url": "https://example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 400
    assert "does not match" in r.json()["detail"]


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

