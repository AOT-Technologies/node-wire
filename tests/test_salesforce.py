from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from node_wire_runtime import SecretProvider
from node_wire_salesforce.logic import SalesforceConnector, SalesforceTransientError
from node_wire_salesforce.schema import (
    CreateLeadInput,
    ReadLeadInput,
    UpdateLeadInput,
    DeleteLeadInput,
    CreateContactInput,
    ReadContactInput,
    UpdateContactInput,
    DeleteContactInput,
    SalesforceOperationOutput,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "salesforce_instance_url": "https://test.salesforce.com",
        }[key]


def _connector() -> SalesforceConnector:
    """Return a SalesforceConnector with mock secrets."""
    conn = SalesforceConnector(secret_provider=MockSecretProvider())
    # Mock auth headers
    conn.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer mock_token"})
    return conn


# ---------------------------------------------------------------------------
# Create Contact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_create_contact_happy_path():
    connector = _connector()
    params = CreateContactInput(LastName="Doe", FirstName="John", Email="john@example.com")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.content = b'{"id": "003123456789012", "success": true}'
    mock_response.json.return_value = {"id": "003123456789012", "success": True}
    mock_response.text = '{"id": "003123456789012", "success": true}'

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.create_contact(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "003123456789012"
    assert result.data["id"] == "003123456789012"


@pytest.mark.asyncio
async def test_salesforce_create_contact_validation_error():
    # Invalid AccountId (too short)
    with pytest.raises(ValidationError) as excinfo:
        CreateContactInput(LastName="Doe", AccountId="short")
    assert "Invalid Salesforce AccountId format" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Update Contact (204 No Content)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_update_contact_204_path():
    connector = _connector()
    params = UpdateContactInput(record_id="003123456789012", fields={"FirstName": "Jane"})

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 204
    mock_response.content = b""
    mock_response.text = ""

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.update_contact(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "003123456789012"
    assert result.data == {}


# ---------------------------------------------------------------------------
# Error Handling (Raises Exception)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_error_raises_exception():
    connector = _connector()
    params = ReadContactInput(record_id="003123456789012")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 400
    mock_response.text = 'Bad Request'
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Bad Request", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient.request", return_value=mock_response):
        with pytest.raises(httpx.HTTPStatusError):
            await connector.read_contact(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# Transient Error (Raises SalesforceTransientError)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_transient_error_raises():
    connector = _connector()
    params = ReadContactInput(record_id="003123456789012")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 503
    mock_response.text = 'Service Unavailable'

    with patch("httpx.AsyncClient.request", return_value=mock_response):
        with pytest.raises(SalesforceTransientError):
            await connector.read_contact(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# End-to-End internal_execute logic (checks mapping)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_internal_execute_mapping():
    connector = _connector()
    # Mocking internal_execute because BaseConnector handles the exception wrapping
    
    params = ReadContactInput(record_id="003123456789012")
    
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 503
    mock_response.text = "Transient Error"
    
    with patch("httpx.AsyncClient.request", return_value=mock_response):
        # We call internal_execute directly to bypass BaseConnector.run's retry logic for now
        # but check that it raises the expected transient error
        with pytest.raises(SalesforceTransientError):
            await connector.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# Delete Contact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_delete_contact_happy_path():
    connector = _connector()
    params = DeleteContactInput(record_id="003123456789012")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 204
    mock_response.content = b""

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.delete_contact(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "003123456789012"
    mock_request.assert_called_once()
    assert mock_request.call_args[0][0] == "DELETE"


# ---------------------------------------------------------------------------
# Lead Operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_salesforce_create_lead_happy_path():
    connector = _connector()
    params = CreateLeadInput(LastName="Smith", Company="Acme Corp", Email="smith@acme.com")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 201
    mock_response.content = b'{"id": "00Q123456789012", "success": true}'
    mock_response.json.return_value = {"id": "00Q123456789012", "success": True}
    mock_response.text = '{"id": "00Q123456789012", "success": true}'

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.create_lead(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "00Q123456789012"
    assert "LastName" in mock_request.call_args[1]["json"]
    assert mock_request.call_args[1]["json"]["LastName"] == "Smith"

@pytest.mark.asyncio
async def test_salesforce_read_lead_happy_path():
    connector = _connector()
    params = ReadLeadInput(record_id="00Q123456789012")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.content = b'{"Id": "00Q123456789012", "LastName": "Smith"}'
    mock_response.json.return_value = {"Id": "00Q123456789012", "LastName": "Smith"}

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.read_lead(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "00Q123456789012"
    assert result.data["LastName"] == "Smith"

@pytest.mark.asyncio
async def test_salesforce_update_lead_happy_path():
    connector = _connector()
    params = UpdateLeadInput(record_id="00Q123456789012", fields={"Company": "New Acme"})

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 204
    mock_response.content = b""

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.update_lead(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "00Q123456789012"
    assert mock_request.call_args[0][0] == "PATCH"
    assert mock_request.call_args[1]["json"]["Company"] == "New Acme"

@pytest.mark.asyncio
async def test_salesforce_delete_lead_happy_path():
    connector = _connector()
    params = DeleteLeadInput(record_id="00Q123456789012")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 204
    mock_response.content = b""

    with patch("httpx.AsyncClient.request", return_value=mock_response) as mock_request:
        result = await connector.delete_lead(params, trace_id="test-trace")

    assert result.success is True
    assert result.resource_id == "00Q123456789012"
    assert mock_request.call_args[0][0] == "DELETE"
