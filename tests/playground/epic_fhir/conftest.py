#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import httpx
import pytest


@pytest.fixture(scope="session", autouse=True)
def epic_fhir_connector_available(api_server_url: str) -> None:
    """Skip the entire Epic FHIR test session if the connector returns HTTP 500.

    This happens when Epic FHIR credentials are missing or when NW_ALLOWED_CONNECTORS
    is set but does not include 'fhir_epic'.
    """
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/post-consultation",
            json={
                "patient_id": "e63wRTbPfr1p8UW81d8Seiw3",
                "encounter_id": "ecgXt3jVqNNpsXnNXZ3KljA3",
                "patient_given": "Jason",
                "patient_family": "Smith",
                "note_text": "health-check",
            },
        )
    if resp.status_code == 500:
        detail = resp.json().get("detail", "unknown")
        pytest.skip(
            f"Epic FHIR connector not available ({detail}). "
            "Ensure Epic credentials are configured and 'fhir_epic' is in NW_ALLOWED_CONNECTORS "
            "(or leave it unset)."
        )
