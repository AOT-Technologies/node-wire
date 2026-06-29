#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from node_wire_fhir_cerner.logic import FhirCernerConnector
from node_wire_fhir_cerner.schema import FhirCernerPatientSearchInput
from node_wire_fhir_epic.logic import FhirEpicConnector
from node_wire_fhir_epic.schema import FhirPatientSearchInput
from node_wire_runtime import SecretProvider
from node_wire_runtime.auth import StaticTokenAuthProvider

PHI_MARKER = "PHI_MARKER_SMITH_SENSITIVE"


class _CernerSecrets(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "cerner_fhir_base_url": "https://fhir-myrecord.cerner.com/r4/tenant-id",
            "cerner_private_key": "dummy",
            "cerner_kid": "dummy-kid",
            "cerner_client_id": "dummy-client-id",
            "cerner_token_url": "https://authorization.cerner.com/token",
            "dummy_token_key": "dummy-access-token",
        }[key]


class _EpicSecrets(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "epic_fhir_base_url": "https://fhir.epic.com/api/FHIR/R4",
            "epic_private_key": "dummy",
            "epic_kid": "dummy-kid",
            "epic_client_id": "dummy-client-id",
            "epic_token_url": "https://fhir.epic.com/token",
            "dummy_token_key": "dummy-access-token",
        }[key]


def _http_status_error(body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/Patient")
    response = httpx.Response(400, request=request, text=body)
    return httpx.HTTPStatusError("error", request=request, response=response)


def _cerner_connector() -> FhirCernerConnector:
    sp = _CernerSecrets()
    auth = StaticTokenAuthProvider(secret_provider=sp, secret_key="dummy_token_key")
    return FhirCernerConnector(secret_provider=sp, auth_provider=auth)


def _epic_connector() -> FhirEpicConnector:
    sp = _EpicSecrets()
    auth = StaticTokenAuthProvider(secret_provider=sp, secret_key="dummy_token_key")
    return FhirEpicConnector(secret_provider=sp, auth_provider=auth)


@pytest.mark.asyncio
async def test_fhir_cerner_name_search_error_does_not_log_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="connectors.fhir_cerner")
    connector = _cerner_connector()
    params = FhirCernerPatientSearchInput(action="search_patients", family_name="Smith")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=_http_status_error(f'{{"diagnostics":"{PHI_MARKER}"}}'),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await connector.internal_execute(params, trace_id="trace-cerner")

    assert PHI_MARKER not in caplog.text


@pytest.mark.asyncio
async def test_fhir_epic_name_search_error_does_not_log_response_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="connectors.fhir_epic")
    connector = _epic_connector()
    params = FhirPatientSearchInput(action="search_patients", family_name="Smith")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=_http_status_error(f'{{"diagnostics":"{PHI_MARKER}"}}'),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await connector.internal_execute(params, trace_id="trace-epic")

    assert PHI_MARKER not in caplog.text
