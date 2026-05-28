#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import httpx
import pytest


@pytest.fixture(scope="session", autouse=True)
def cerner_connector_available(api_server_url: str) -> None:
    """Skip the entire Cerner test session if the connector returns HTTP 500.

    This happens when Cerner FHIR credentials are missing or when NW_ALLOWED_CONNECTORS
    is set but does not include 'fhir_cerner'.
    """
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/cerner-post-consultation",
            json={
                "patient_id": "12724066",
                "encounter_id": "97957281",
                "patient_given": "Nancy",
                "patient_family": "Smart",
                "note_text": "health-check",
            },
        )
    if resp.status_code == 500:
        detail = resp.json().get("detail", "unknown")
        pytest.skip(
            f"Cerner connector not available ({detail}). "
            "Ensure Cerner credentials are configured and 'fhir_cerner' is in NW_ALLOWED_CONNECTORS "
            "(or leave it unset)."
        )
