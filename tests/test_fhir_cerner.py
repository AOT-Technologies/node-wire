#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from node_wire_fhir_cerner.logic import FhirCernerConnector
from node_wire_runtime import SecretProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "cerner_fhir_base_url": "https://fhir-myrecord.cerner.com/r4/tenant-id",
            "cerner_private_key": "-----BEGIN RSA PRIVATE KEY-----\\nMEowIQ...dummy\\n-----END RSA PRIVATE KEY-----",
            "cerner_kid": "dummy-kid",
            "cerner_client_id": "dummy-client-id",
            "cerner_token_url": "https://authorization.cerner.com/tenants/tenant-id/protocols/oauth2/profiles/smart-v1/token",
            "dummy_token_key": "dummy-access-token",
        }[key]


def _token_mock() -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"access_token": "dummy-access-token"}
    return m


def _connector() -> FhirCernerConnector:
    """Return a FhirCernerConnector with a static mock token."""
    from node_wire_runtime.auth import StaticTokenAuthProvider

    sp = MockSecretProvider()
    auth = StaticTokenAuthProvider(
        secret_provider=sp,
        secret_key="dummy_token_key",
    )
    return FhirCernerConnector(secret_provider=sp, auth_provider=auth)


def _token_mock() -> MagicMock:
    """Not used by StaticTokenAuthProvider, but kept for compatibility."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"access_token": "dummy-access-token"}
    return m


# ---------------------------------------------------------------------------
# Sanity: unified connector (single execute entrypoint)
# ---------------------------------------------------------------------------


def test_fhir_cerner_connector_is_unified_execute():
    c = _connector()
    assert c.connector_id == "fhir_cerner"
    assert c.action == "execute"


# ---------------------------------------------------------------------------
# read_patient — by ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_read_patient_by_id():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(action="read_patient", resource_id="12345678")

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Patient",
        "id": "12345678",
        "name": [{"family": "Smith"}],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "12345678"
    assert result.resource["name"][0]["family"] == "Smith"


# ---------------------------------------------------------------------------
# read_patient — by raw search_params dict (backward-compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_read_patient_by_search():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(
        action="read_patient",
        search_params={"family": "Smith", "given": "John"},
    )

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [{"resource": {"resourceType": "Patient", "id": "99887766"}}],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "99887766"


# ---------------------------------------------------------------------------
# read_patient — by explicit given_name / family_name fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_read_patient_by_explicit_name_fields():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(
        action="read_patient",
        given_name="  Jane  ",
        family_name="Doe",
        birthdate="1990-06-15",
    )

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "55551234", "birthDate": "1990-06-15"}}
        ],
    }

    with (
        patch("node_wire_fhir_cerner.logic.jwt.encode", return_value="dummy-jwt"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()),
        patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response
        ) as mock_get,
    ):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "55551234"
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("given") == "Jane"  # whitespace stripped
    assert sent_params.get("family") == "Doe"
    assert sent_params.get("birthdate") == "1990-06-15"


# ---------------------------------------------------------------------------
# read_patient — by 'name' convenience field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_read_patient_by_name_field():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(action="read_patient", name="Johnson")

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [{"resource": {"resourceType": "Patient", "id": "99990001"}}],
    }

    with (
        patch("node_wire_fhir_cerner.logic.jwt.encode", return_value="dummy-jwt"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()),
        patch(
            "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response
        ) as mock_get,
    ):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "99990001"
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("name") == "Johnson"


# ---------------------------------------------------------------------------
# read_patient — no params raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_read_patient_no_params_raises():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(action="read_patient")

    with (
        patch("node_wire_fhir_cerner.logic.jwt.encode", return_value="dummy-jwt"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()),
    ):
        with pytest.raises(ValueError, match="Provide resource_id"):
            await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# search_patients — multi-ID, all succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_patients_multi_id():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput

    params = FhirCernerPatientSearchInput(
        action="search_patients",
        resource_ids=["11111111", "22222222"],
    )

    def _patient_resp(pid: str) -> MagicMock:
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"resourceType": "Patient", "id": pid}
        return m

    responses = [_patient_resp("11111111"), _patient_resp("22222222")]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=responses):
        result = await c.internal_execute(params, trace_id="test-trace")

    ids = {r["id"] for r in result.resources}
    assert ids == {"11111111", "22222222"}
    assert result.total == 2
    assert result.errors == []


# ---------------------------------------------------------------------------
# search_patients — multi-ID, partial failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_patients_partial_failure():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput

    params = FhirCernerPatientSearchInput(
        action="search_patients",
        resource_ids=["99999999", "00000000"],
    )

    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.json.return_value = {"resourceType": "Patient", "id": "99999999"}

    bad_resp = MagicMock()
    bad_resp.status_code = 404
    bad_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[good_resp, bad_resp]):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert len(result.resources) == 1
    assert result.resources[0]["id"] == "99999999"
    assert len(result.errors) == 1
    assert result.errors[0]["resource_id"] == "00000000"


# ---------------------------------------------------------------------------
# search_patients — name-based search returning multiple Bundle entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_patients_by_name():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput

    params = FhirCernerPatientSearchInput(action="search_patients", family_name="Smith")

    bundle_resp = MagicMock()
    bundle_resp.status_code = 200
    bundle_resp.json.return_value = {
        "resourceType": "Bundle",
        "total": 2,
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "11111111",
                    "name": [{"family": "Smith", "given": ["Alice"]}],
                }
            },
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "22222222",
                    "name": [{"family": "Smith", "given": ["Bob"]}],
                }
            },
        ],
    }

    with patch(
        "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=bundle_resp
    ) as mock_get:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 2
    assert len(result.resources) == 2
    assert result.errors == []
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("family") == "Smith"


# ---------------------------------------------------------------------------
# search_patients — no params raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_patients_no_params_raises():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput

    params = FhirCernerPatientSearchInput(action="search_patients")

    with (
        patch("node_wire_fhir_cerner.logic.jwt.encode", return_value="dummy-jwt"),
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()),
    ):
        with pytest.raises(ValueError):
            await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# search_encounter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_encounter():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerEncounterSearchInput

    params = FhirCernerEncounterSearchInput(
        action="search_encounter",
        search_params={"patient": "12345678", "status": "finished"},
    )

    enc_response = MagicMock()
    enc_response.status_code = 200
    enc_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 2,
        "entry": [
            {"resource": {"resourceType": "Encounter", "id": "enc-1"}},
            {"resource": {"resourceType": "Encounter", "id": "enc-2"}},
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=enc_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 2
    assert result.resources[0]["id"] == "enc-1"


@pytest.mark.asyncio
async def test_fhir_cerner_search_encounter_rejects_unscoped_query():
    """Status-only encounter search must not call the FHIR server (enterprise guard)."""
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerEncounterSearchInput

    params = FhirCernerEncounterSearchInput(action="search_encounter", status="finished")
    with pytest.raises(ValueError, match="patient-scoped"):
        await c.internal_execute(params, trace_id="test-trace")


@pytest.mark.asyncio
async def test_fhir_cerner_search_encounter_by_patient():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerEncounterSearchInput

    params = FhirCernerEncounterSearchInput(action="search_encounter", patient_id="12345678")

    enc_response = MagicMock()
    enc_response.status_code = 200
    enc_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [{"resource": {"resourceType": "Encounter", "id": "enc-1"}}],
    }

    with patch(
        "httpx.AsyncClient.get", new_callable=AsyncMock, return_value=enc_response
    ) as mock_get:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 1
    assert result.resources[0]["id"] == "enc-1"
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("patient") == "12345678"


# ---------------------------------------------------------------------------
# create_document_reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_create_document_reference():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerDocumentReferenceCreateInput

    params = FhirCernerDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": "ID.123"}],
        status="current",
        doc_status="final",
        type={
            "coding": [
                {
                    "system": "urn:oid:4.5.6",
                    "code": "18100",
                    "display": "Employer Group Scan",
                    "userSelected": True,
                }
            ],
            "text": "Employer Group Scan",
        },
        subject="Patient/12724066",
        data="dGVzdA==",
        attachment_title="Document",
        author=[{"reference": "Practitioner/p1"}],
        context={
            "encounter": [{"reference": "Encounter/enc-1"}],
            "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},
        },
    )

    create_response = MagicMock()
    create_response.status_code = 201
    create_response.headers = {
        "Location": "https://fhir-myrecord.cerner.com/r4/tenant-id/DocumentReference/doc-456/_history/1"
    }
    create_response.content = b""
    create_response.text = ""

    with patch(
        "httpx.AsyncClient.post", new_callable=AsyncMock, return_value=create_response
    ) as mock_post:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource_id == "doc-456"
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["resourceType"] == "DocumentReference"
    assert kwargs["json"]["subject"] == {"reference": "Patient/12724066"}
    # Verify that charset was added to contentType
    assert kwargs["json"]["content"][0]["attachment"]["contentType"] == "text/plain;charset=utf-8"


# ---------------------------------------------------------------------------
# search_document_reference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_search_document_reference():
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerDocumentReferenceSearchInput

    params = FhirCernerDocumentReferenceSearchInput(
        action="search_document_reference",
        search_params={"patient": "12345678"},
    )

    search_response = MagicMock()
    search_response.status_code = 200
    search_response.json.return_value = {
        "resourceType": "Bundle",
        "total": 1,
        "entry": [
            {
                "resource": {
                    "resourceType": "DocumentReference",
                    "id": "doc-789",
                    "status": "current",
                    "type": {"coding": [{"system": "urn:oid:4.5.6", "code": "18100"}]},
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=search_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 1
    assert result.resources[0]["id"] == "doc-789"


# ---------------------------------------------------------------------------
# Validation: LOINC system rejected for Cerner (context.period auto-inject)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_create_document_reference_validation():
    """Verify that ValueError is raised when period is missing but encounter is present."""
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerDocumentReferenceCreateInput

    params = FhirCernerDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": "ID.123"}],
        status="current",
        doc_status="final",
        type={"coding": [{"system": "http://loinc.org", "code": "11488-4"}]},
        subject="Patient/12724066",
        data="dGVzdA==",
        attachment_title="Doc",
        author=[{"reference": "Practitioner/p1"}],
        context={"encounter": [{"reference": "Encounter/enc-1"}]},
    )

    with pytest.raises(ValueError, match="Cerner requires the proprietary CodeSet 72 system"):
        await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# Auth: malformed token URL (/hosts/)
# ---------------------------------------------------------------------------


class MalformedCernerTokenUrlProvider(SecretProvider):
    """Token URL containing ``/hosts/`` triggers a clear configuration error."""

    def __init__(self) -> None:
        self._defaults = MockSecretProvider()

    def get_secret(self, key: str) -> str:
        if key == "cerner_token_url":
            return (
                "https://authorization.cerner.com/tenants/x/hosts/fhir-ehr-code.cerner.com/"
                "protocols/oauth2/profiles/smart-v1/token"
            )
        return self._defaults.get_secret(key)


@pytest.mark.asyncio
async def test_fhir_cerner_auth_rejects_malformed_token_url_with_hosts_segment() -> None:
    from node_wire_runtime.auth import OAuth2AuthProvider

    sp = MalformedCernerTokenUrlProvider()
    auth = OAuth2AuthProvider(
        secret_provider=sp,
        grant_method="private_key_jwt",
        token_url_secret="cerner_token_url",
        client_id_secret="cerner_client_id",
        private_key_secret="cerner_private_key",
        kid_secret="cerner_kid",
        algorithm="RS384",
    )
    c = FhirCernerConnector(secret_provider=sp, auth_provider=auth)
    from node_wire_fhir_cerner.schema import FhirCernerPatientReadInput

    params = FhirCernerPatientReadInput(action="read_patient", resource_id="123")
    with patch("node_wire_runtime.auth.oauth2.jwt.encode", return_value="dummy-assertion"):
        with pytest.raises(ValueError, match="/hosts/"):
            await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# create_document_reference: OperationOutcome on HTTP error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_cerner_create_document_reference_operation_outcome_error() -> None:
    c = _connector()
    from node_wire_fhir_cerner.schema import FhirCernerDocumentReferenceCreateInput

    params = FhirCernerDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": "ID.123"}],
        status="current",
        doc_status="final",
        type={
            "coding": [
                {
                    "system": "urn:oid:4.5.6",
                    "code": "18100",
                    "display": "Employer Group Scan",
                    "userSelected": True,
                }
            ],
            "text": "Employer Group Scan",
        },
        subject="Patient/12724066",
        data="dGVzdA==",
        attachment_title="Document",
        author=[{"reference": "Practitioner/p1"}],
        context={
            "encounter": [{"reference": "Encounter/enc-1"}],
            "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},
        },
    )

    post_req = httpx.Request("POST", "https://fhir.example/DocumentReference")
    err_resp = httpx.Response(
        400,
        request=post_req,
        json={
            "resourceType": "OperationOutcome",
            "issue": [
                {"severity": "error", "code": "invalid", "diagnostics": "Cerner rejected payload"}
            ],
        },
    )

    async def post_side_effect(*args: object, **kwargs: object) -> httpx.Response | MagicMock:
        post_side_effect.calls += 1
        if post_side_effect.calls == 1:
            return _token_mock()
        return err_resp

    post_side_effect.calls = 0

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=err_resp):
        with pytest.raises(ValueError, match="Cerner Error:.*Cerner rejected payload"):
            await c.internal_execute(params, trace_id="test-trace")
