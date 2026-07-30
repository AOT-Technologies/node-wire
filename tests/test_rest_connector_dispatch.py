#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""REST dispatch smoke tests for all eight publishable connectors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from bindings.rest_api.app import app, get_factory
from node_wire_runtime.models import ConnectorResponse

_ALL_EIGHT_CONNECTOR_IDS = (
    "http_generic",
    "smtp",
    "stripe",
    "google_drive",
    "fhir_epic",
    "fhir_cerner",
    "salesforce",
    "slack",
)

_REST_SMOKE_CASES = [
    pytest.param(
        "http_generic",
        "request",
        "/connectors/http_generic/request",
        {"method": "GET", "url": "https://example.com"},
        id="http_generic",
    ),
    pytest.param(
        "smtp",
        "send_email",
        "/connectors/smtp/send_email",
        {
            "from_email": "a@example.com",
            "to": ["b@example.com"],
            "subject": "s",
            "body": "hi",
        },
        id="smtp",
    ),
    pytest.param(
        "stripe",
        "charge",
        "/connectors/stripe/charge",
        {"amount": 100, "currency": "usd", "source": "tok_visa"},
        id="stripe",
    ),
    pytest.param(
        "google_drive",
        "files.list",
        "/connectors/google_drive/files.list",
        {"action": "files.list", "page_size": 1},
        id="google_drive",
    ),
    pytest.param(
        "fhir_epic",
        "read_patient",
        "/connectors/fhir_epic/read_patient",
        {"resource_id": "123"},
        id="fhir_epic",
    ),
    pytest.param(
        "fhir_cerner",
        "read_patient",
        "/connectors/fhir_cerner/read_patient",
        {"resource_id": "123"},
        id="fhir_cerner",
    ),
    pytest.param(
        "salesforce",
        "create_contact",
        "/connectors/salesforce/create_contact",
        {"LastName": "Doe"},
        id="salesforce",
    ),
    pytest.param(
        "slack",
        "post_message",
        "/connectors/slack/post_message",
        {"channel": "C0TEST123", "message": "hi"},
        id="slack",
    ),
]


def _stub_connector(response: ConnectorResponse) -> MagicMock:
    connector = MagicMock()
    connector.run = AsyncMock(return_value=response)
    return connector


@pytest.mark.parametrize(
    ("connector_id", "action", "route_path", "payload"),
    _REST_SMOKE_CASES,
)
def test_rest_dispatch_smoke_per_connector(
    connector_id: str,
    action: str,
    route_path: str,
    payload: dict,
) -> None:
    mock_factory = MagicMock()
    mock_factory.is_exposed.return_value = True
    mock_factory.get = AsyncMock(
        return_value=_stub_connector(
            ConnectorResponse(success=True, data={"ok": True}, trace_id=f"t-{connector_id}")
        )
    )
    app.dependency_overrides[get_factory] = lambda: mock_factory
    try:
        client = TestClient(app)
        response = client.post(route_path, json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["success"] is True
    mock_factory.get.assert_awaited_with(
        connector_id, tenant_id="__default__", config_name=None, action=action
    )


@pytest.mark.parametrize(
    ("connector_id", "action", "route_path", "_payload"),
    _REST_SMOKE_CASES,
)
def test_rest_routes_registered_for_all_connectors(
    connector_id: str,
    action: str,
    route_path: str,
    _payload: dict,
) -> None:
    registered_paths = {getattr(route, "path", None) for route in app.routes}
    assert route_path in registered_paths, f"missing REST route for {connector_id}.{action}"
