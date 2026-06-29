#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from bindings.rest_api import app as rest_app_module
from bindings.rest_api.app import app, get_factory
from bindings.rest_api.rate_limit import InMemoryRateLimiter
from node_wire_runtime.models import ConnectorResponse


def _stub_connector() -> MagicMock:
    connector = MagicMock()
    connector.run = AsyncMock(
        return_value=ConnectorResponse(success=True, data={"ok": True}, trace_id="t-limit")
    )
    return connector


def _make_client(monkeypatch) -> tuple[TestClient, MagicMock]:
    monkeypatch.setenv("NW_REST_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_MAX_REQUESTS", "2")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setattr(rest_app_module, "_rate_limiter", None)
    monkeypatch.setattr(rest_app_module, "_rate_limiter_cfg", None)

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector()
    app.dependency_overrides[get_factory] = lambda: mock_factory
    return TestClient(app), mock_factory


def test_rest_rate_limit_allows_under_threshold(monkeypatch) -> None:
    client, _ = _make_client(monkeypatch)
    try:
        first = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
        second = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == 200
    assert second.status_code == 200


def test_rest_rate_limit_returns_429_and_retry_after(monkeypatch) -> None:
    client, _ = _make_client(monkeypatch)
    try:
        client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
        client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
        third = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
    finally:
        app.dependency_overrides.clear()

    assert third.status_code == 429
    assert third.json()["detail"] == "Rate limit exceeded"
    retry_after = third.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1


def test_rest_rate_limit_isolated_by_identity(monkeypatch) -> None:
    monkeypatch.setenv("NW_REST_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setattr(rest_app_module, "_rate_limiter", None)
    monkeypatch.setattr(rest_app_module, "_rate_limiter_cfg", None)

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector()
    app.dependency_overrides[get_factory] = lambda: mock_factory

    try:
        client = TestClient(app)
        first = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-a"},
        )
        second = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-API-Key": "tenant-b"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200


def test_rate_limiter_evicts_when_max_keys_exceeded() -> None:
    limiter = InMemoryRateLimiter(
        max_requests=10,
        window_seconds=60,
        max_tracked_keys=2,
        key_ttl_seconds=3600,
    )
    assert limiter.consume("key-a").allowed is True
    assert limiter.consume("key-b").allowed is True
    assert limiter.tracked_key_count == 2

    assert limiter.consume("key-c").allowed is True
    assert limiter.tracked_key_count == 2
    assert "key-a" not in limiter._buckets  # noqa: SLF001

    assert limiter.consume("key-a").allowed is True
    assert limiter.tracked_key_count == 2


def test_rate_limiter_evicts_idle_keys_after_ttl() -> None:
    limiter = InMemoryRateLimiter(
        max_requests=10,
        window_seconds=60,
        max_tracked_keys=10,
        key_ttl_seconds=1,
    )
    times = iter([100.0, 103.0])
    with patch("bindings.rest_api.rate_limit.monotonic", side_effect=lambda: next(times)):
        assert limiter.consume("idle-key").allowed is True
        assert limiter.tracked_key_count == 1
        assert limiter.consume("fresh-key").allowed is True
        assert limiter.tracked_key_count == 1
        assert "idle-key" not in limiter._buckets  # noqa: SLF001
        assert "fresh-key" in limiter._buckets  # noqa: SLF001


def test_rest_rate_limit_ignores_spoofed_xff_when_proxy_hops_zero(monkeypatch) -> None:
    monkeypatch.setenv("NW_REST_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("NW_REST_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "0")
    monkeypatch.setattr(rest_app_module, "_rate_limiter", None)
    monkeypatch.setattr(rest_app_module, "_rate_limiter_cfg", None)

    mock_factory = MagicMock()
    mock_factory.get_for_protocol.return_value = _stub_connector()
    app.dependency_overrides[get_factory] = lambda: mock_factory

    try:
        client = TestClient(app)
        first = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-Forwarded-For": "203.0.113.1"},
        )
        second = client.post(
            "/connectors/http_generic/request",
            json={"method": "GET", "url": "https://example.com"},
            headers={"X-Forwarded-For": "203.0.113.2"},
        )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 429
