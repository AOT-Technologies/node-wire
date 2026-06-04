#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import httpx
import pytest


@pytest.fixture(scope="session", autouse=True)
def http_connector_available(api_server_url: str) -> None:
    """Skip the entire HTTP connector test session if the connector returns HTTP 500.

    This happens when NW_ALLOWED_CONNECTORS is set but does not include 'http_generic'.
    """
    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/report-incident",
            json={
                "title": "health-check",
                "severity": "HIGH",
                "component": "Gateway Proxy",
                "description": "health-check",
                "reported_by": "DevOps Team Alpha",
            },
        )
    if resp.status_code == 500:
        detail = resp.json().get("detail", "unknown")
        pytest.skip(
            f"HTTP connector not available ({detail}). "
            "Ensure 'http_generic' is in NW_ALLOWED_CONNECTORS (or leave it unset)."
        )
