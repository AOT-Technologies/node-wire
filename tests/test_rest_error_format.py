# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""REST failures: the status REST always returned, the envelope, and ``detail``."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


from bindings.invoke import ConnectorNotExposed
from bindings.rest_api.app import app, get_factory

_UUID = r"[0-9a-f-]{36}"


def _post(factory: MagicMock, **kwargs: object):  # noqa: ANN202
    app.dependency_overrides[get_factory] = lambda: factory
    try:
        return TestClient(app).post("/connectors/http_generic/request", **kwargs)
    finally:
        app.dependency_overrides.clear()


def _envelope(resp, status: int, code: str, category: str) -> dict:  # noqa: ANN001
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert (body["success"], body["error_code"], body["error_category"]) == (False, code, category)
    assert re.fullmatch(_UUID, body["trace_id"])
    assert body["detail"] == body["message"]  # kept for clients of the plain FastAPI errors
    return body


def test_missing_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    factory = MagicMock()
    factory.is_exposed.return_value = True
    _envelope(_post(factory, json={}), 400, "MISSING_TENANT", "AUTH")


def test_connector_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    factory = MagicMock()
    factory.is_exposed.return_value = True
    factory.get = AsyncMock(side_effect=ConnectorNotExposed("http_generic", "rest"))
    body = _envelope(_post(factory, json={}), 404, "CONNECTOR_NOT_AVAILABLE", "BUSINESS")
    assert body["detail"] == "Connector not available for REST"
