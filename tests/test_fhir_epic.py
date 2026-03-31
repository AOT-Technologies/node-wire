from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from connectors.fhir_epic.logic import FhirEpicConnector
from runtime import SecretProvider


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "epic_fhir_base_url": "https://fhir.epic.com/api/FHIR/R4",
            "epic_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMEowIQ...dummy\n-----END RSA PRIVATE KEY-----",
            "epic_kid": "dummy-kid",
            "epic_client_id": "dummy-client-id",
            "epic_token_url": "https://fhir.epic.com/token",
        }[key]


def _token_mock() -> MagicMock:
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"access_token": "dummy-access-token"}
    return m


def _connector() -> FhirEpicConnector:
    """Return a FhirEpicConnector with mock secrets."""
    return FhirEpicConnector(secret_provider=MockSecretProvider())


# ---------------------------------------------------------------------------
# Sanity: unified connector (single execute entrypoint)
# ---------------------------------------------------------------------------

def test_fhir_epic_connector_is_unified_execute():
    c = _connector()
    assert c.connector_id == "fhir_epic"
    assert c.action == "execute"


# ---------------------------------------------------------------------------
# read_patient — by ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_read_patient_by_id():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientReadInput
    params = FhirPatientReadInput(action="read_patient", resource_id="eXYZ123")

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {"resourceType": "Patient", "id": "eXYZ123", "name": [{"family": "Smith"}]}

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "eXYZ123"
    assert result.resource["name"][0]["family"] == "Smith"


# ---------------------------------------------------------------------------
# read_patient — by raw search_params dict (backward-compat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_read_patient_by_search():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientReadInput
    params = FhirPatientReadInput(
        action="read_patient",
        search_params={"family": "Smith", "given": "John"},
    )

    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle", "total": 1,
        "entry": [{"resource": {"resourceType": "Patient", "id": "eABC"}}],
    }

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "eABC"


# ---------------------------------------------------------------------------
# read_patient — by explicit given_name / family_name fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_read_patient_by_explicit_name_fields():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientReadInput
    params = FhirPatientReadInput(
        action="read_patient",
        given_name="  John  ",
        family_name="Smith",
        birthdate="1980-01-01",
    )
 
    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle", "total": 1,
        "entry": [{"resource": {"resourceType": "Patient", "id": "eDEF", "birthDate": "1980-01-01"}}],
    }
 
    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response) as mock_get:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "eDEF"
    # Verify the correct FHIR params were built (stripped whitespace)
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("given") == "John"
    assert sent_params.get("family") == "Smith"
    assert sent_params.get("birthdate") == "1980-01-01"


# ---------------------------------------------------------------------------
# read_patient — by 'name' convenience field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_read_patient_by_name_field():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientReadInput
    params = FhirPatientReadInput(action="read_patient", name="Johnson")
 
    patient_response = MagicMock()
    patient_response.status_code = 200
    patient_response.json.return_value = {
        "resourceType": "Bundle", "total": 1,
        "entry": [{"resource": {"resourceType": "Patient", "id": "eGHI"}}],
    }
 
    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=patient_response) as mock_get:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource["id"] == "eGHI"
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("name") == "Johnson"


# ---------------------------------------------------------------------------
# read_patient — no params raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_read_patient_no_params_raises():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientReadInput
    params = FhirPatientReadInput(action="read_patient")

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()):
        with pytest.raises(ValueError, match="Provide resource_id"):
            await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# search_patients — multi-ID, all succeed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_patients_multi_id():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientSearchInput
    params = FhirPatientSearchInput(action="search_patients", resource_ids=["eABC", "eDEF"])

    def _patient_resp(pid: str) -> MagicMock:
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"resourceType": "Patient", "id": pid}
        return m

    responses = [_patient_resp("eABC"), _patient_resp("eDEF")]

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=responses):
        result = await c.internal_execute(params, trace_id="test-trace")

    ids = {r["id"] for r in result.resources}
    assert ids == {"eABC", "eDEF"}
    assert result.total == 2
    assert result.errors == []


# ---------------------------------------------------------------------------
# search_patients — multi-ID, partial failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_patients_partial_failure():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientSearchInput
    params = FhirPatientSearchInput(action="search_patients", resource_ids=["eGOOD", "eBAD"])

    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.json.return_value = {"resourceType": "Patient", "id": "eGOOD"}

    bad_resp = MagicMock()
    bad_resp.status_code = 404
    bad_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=[good_resp, bad_resp]):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert len(result.resources) == 1
    assert result.resources[0]["id"] == "eGOOD"
    assert len(result.errors) == 1
    assert result.errors[0]["resource_id"] == "eBAD"


# ---------------------------------------------------------------------------
# search_patients — name-based search returning multiple entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_patients_by_name():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientSearchInput
    params = FhirPatientSearchInput(action="search_patients", family_name="Smith")

    bundle_resp = MagicMock()
    bundle_resp.status_code = 200
    bundle_resp.json.return_value = {
        "resourceType": "Bundle",
        "total": 2,
        "entry": [
            {"resource": {"resourceType": "Patient", "id": "e001", "name": [{"family": "Smith", "given": ["Alice"]}]}},
            {"resource": {"resourceType": "Patient", "id": "e002", "name": [{"family": "Smith", "given": ["Bob"]}]}},
        ],
    }

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=bundle_resp) as mock_get:
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 2
    assert len(result.resources) == 2
    assert result.errors == []
    # Verify correct FHIR param was sent
    call_kwargs = mock_get.call_args
    sent_params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
    assert sent_params.get("family") == "Smith"


# ---------------------------------------------------------------------------
# search_patients — no params raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_patients_no_params_raises():
    c = _connector()
    from connectors.fhir_epic.schema import FhirPatientSearchInput
    params = FhirPatientSearchInput(action="search_patients")

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()):
        with pytest.raises(ValueError):
            await c.internal_execute(params, trace_id="test-trace")


# ---------------------------------------------------------------------------
# search_encounter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_encounter():
    c = _connector()
    from connectors.fhir_epic.schema import FhirEncounterSearchInput
    params = FhirEncounterSearchInput(
        action="search_encounter",
        search_params={"patient": "eXYZ123", "status": "finished"},
    )

    enc_response = MagicMock()
    enc_response.status_code = 200
    enc_response.json.return_value = {
        "resourceType": "Bundle", "total": 2,
        "entry": [
            {"resource": {"resourceType": "Encounter", "id": "enc-1"}},
            {"resource": {"resourceType": "Encounter", "id": "enc-2"}},
        ],
    }

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=enc_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 2
    assert result.resources[0]["id"] == "enc-1"


# ---------------------------------------------------------------------------
# create_document_reference
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_create_document_reference():
    c = _connector()
    from connectors.fhir_epic.schema import FhirDocumentReferenceCreateInput
    params = FhirDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": "ID.123"}],
        status="current",
        type={"coding": [{"system": "urn:oid:4.5.6", "code": "18100", "display": "Employer Group Scan"}]},
        subject="Patient/ePD0eeFq.GMHG.aXttqP.Lw3",
        data="dGVzdA==",
        context={"related": [{"reference": "Group/eqv3buSV"}]},
    )

    create_response = MagicMock()
    create_response.status_code = 201
    create_response.headers = {"Location": "https://fhir.epic.com/api/FHIR/R4/DocumentReference/doc-456/_history/1"}
    create_response.content = b""
    create_response.text = ""

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = [_token_mock(), create_response]
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.resource_id == "doc-456"
    _, kwargs = mock_post.call_args_list[1]
    assert kwargs["json"]["resourceType"] == "DocumentReference"
    assert kwargs["json"]["subject"] == {"reference": "Patient/ePD0eeFq.GMHG.aXttqP.Lw3"}


# ---------------------------------------------------------------------------
# search_document_reference
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fhir_epic_search_document_reference():
    c = _connector()
    from connectors.fhir_epic.schema import FhirDocumentReferenceSearchInput
    params = FhirDocumentReferenceSearchInput(
        action="search_document_reference",
        search_params={"patient": "eXYZ123"},
    )

    search_response = MagicMock()
    search_response.status_code = 200
    search_response.json.return_value = {
        "resourceType": "Bundle", "total": 1,
        "entry": [{"resource": {"resourceType": "DocumentReference", "id": "doc-789", "status": "current",
                                "type": {"coding": [{"system": "urn:oid:4.5.6", "code": "18100"}]}}}],
    }

    with patch("connectors.fhir_epic.logic.jwt.encode", return_value="dummy-jwt"), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=_token_mock()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=search_response):
        result = await c.internal_execute(params, trace_id="test-trace")

    assert result.total == 1
    assert result.resources[0]["id"] == "doc-789"
