from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from node_wire_fhir_cerner.logic import FhirCernerConnector
from node_wire_fhir_epic.logic import FhirEpicConnector
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth import StaticTokenAuthProvider


class EpicSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "epic_fhir_base_url": "https://fhir.epic.com/api/FHIR/R4",
            "epic_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMEowIQ...dummy\n-----END RSA PRIVATE KEY-----",
            "epic_kid": "dummy-kid",
            "epic_client_id": "dummy-client-id",
            "epic_token_url": "https://fhir.epic.com/token",
            "dummy_token_key": "dummy-access-token",
        }[key]


class CernerSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "cerner_fhir_base_url": "https://fhir-myrecord.cerner.com/r4/tenant-id",
            "cerner_private_key": "-----BEGIN RSA PRIVATE KEY-----\\nMEowIQ...dummy\\n-----END RSA PRIVATE KEY-----",
            "cerner_kid": "dummy-kid",
            "cerner_client_id": "dummy-client-id",
            "cerner_token_url": "https://authorization.cerner.com/tenants/tenant-id/protocols/oauth2/profiles/smart-v1/token",
            "dummy_token_key": "dummy-access-token",
        }[key]


def _epic_connector_for_redaction() -> FhirEpicConnector:
    sp = EpicSecretProvider()
    auth = StaticTokenAuthProvider(secret_provider=sp, secret_key="dummy_token_key")
    return FhirEpicConnector(secret_provider=sp, auth_provider=auth)


def _cerner_connector_for_redaction() -> FhirCernerConnector:
    sp = CernerSecretProvider()
    auth = StaticTokenAuthProvider(secret_provider=sp, secret_key="dummy_token_key")
    return FhirCernerConnector(secret_provider=sp, auth_provider=auth)


def _serialize_calls(mocked_logger: MagicMock) -> str:
    parts: list[str] = []
    for call in mocked_logger.call_args_list:
        parts.append(repr(call.args))
        parts.append(repr(call.kwargs))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_fhir_epic_create_document_reference_logs_redacted_payload() -> None:
    from node_wire_fhir_epic.schema import FhirDocumentReferenceCreateInput

    connector = _epic_connector_for_redaction()
    payload_secret = "SENSITIVE_PAYLOAD_VALUE"
    response_secret = "SENSITIVE_RESPONSE_VALUE"
    data_b64 = base64.b64encode(payload_secret.encode()).decode("ascii")

    params = FhirDocumentReferenceCreateInput(
        action="create_document_reference",
        identifier=[{"system": "urn:oid:1.2.3", "value": "ID.123"}],
        status="current",
        type={
            "coding": [
                {"system": "urn:oid:4.5.6", "code": "18100", "display": "Employer Group Scan"}
            ]
        },
        subject="Patient/ePD0eeFq.GMHG.aXttqP.Lw3",
        data=data_b64,
        context={"related": [{"reference": "Group/eqv3buSV"}]},
    )

    post_req = httpx.Request("POST", "https://fhir.example/DocumentReference")
    err_resp = httpx.Response(400, request=post_req, text=response_secret)

    async def post_side_effect(*args: object, **kwargs: object) -> httpx.Response:
        # StaticTokenAuthProvider: no separate OAuth POST; only FHIR create hits AsyncClient.post.
        return err_resp

    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=post_side_effect),
        patch("node_wire_fhir_epic.logic.logger.error") as mocked_error,
    ):
        with pytest.raises(ValueError, match="Epic Error: HTTP 400 from Epic FHIR endpoint"):
            await connector.internal_execute(params, trace_id="test-trace")

    logged = _serialize_calls(mocked_error)
    assert payload_secret not in logged
    assert data_b64 not in logged
    assert response_secret not in logged
    assert "payload_summary" in logged


@pytest.mark.asyncio
async def test_fhir_cerner_create_document_reference_logs_redacted_payload() -> None:
    from node_wire_fhir_cerner.schema import FhirCernerDocumentReferenceCreateInput

    connector = _cerner_connector_for_redaction()
    payload_secret = "CERNER_SECRET_PAYLOAD"
    response_secret = "CERNER_SECRET_RESPONSE"
    data_b64 = base64.b64encode(payload_secret.encode()).decode("ascii")

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
        data=data_b64,
        attachment_title="Document",
        author=[{"reference": "Practitioner/p1"}],
        context={
            "encounter": [{"reference": "Encounter/enc-1"}],
            "period": {"start": "2024-01-01T00:00:00Z", "end": "2024-01-01T01:00:00Z"},
        },
    )

    post_req = httpx.Request("POST", "https://fhir.example/DocumentReference")
    err_resp = httpx.Response(400, request=post_req, text=response_secret)

    async def post_side_effect(*args: object, **kwargs: object) -> httpx.Response:
        return err_resp

    with (
        patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=post_side_effect),
        patch("node_wire_fhir_cerner.logic.logger.error") as mocked_error,
    ):
        with pytest.raises(ValueError, match="Cerner Error: HTTP 400 from Cerner FHIR endpoint"):
            await connector.internal_execute(params, trace_id="test-trace")

    logged = _serialize_calls(mocked_error)
    assert payload_secret not in logged
    assert data_b64 not in logged
    assert response_secret not in logged
    assert "payload_summary" in logged
