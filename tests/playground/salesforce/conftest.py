#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import httpx
import pytest

from tests.playground.salesforce.helpers import rnd as _rnd


def _create_lead(api_server_url: str, last_name: str, company: str) -> str:
    """Create a Salesforce Lead via the REST API and return its record ID."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/salesforce-create-lead",
            json={"last_name": last_name, "company": company},
        )
    resp.raise_for_status()
    data = resp.json()
    record_id = data.get("final_resource_id")
    if not record_id:
        pytest.skip(
            f"Salesforce Lead creation failed — cannot run dependent tests. "
            f"Error: {data.get('error_message') or 'no record ID returned'}"
        )
    return record_id


def _create_contact(api_server_url: str, last_name: str) -> str:
    """Create a Salesforce Contact via the REST API and return its record ID."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/salesforce-create-contact",
            json={"last_name": last_name},
        )
    resp.raise_for_status()
    data = resp.json()
    record_id = data.get("final_resource_id")
    if not record_id:
        pytest.skip(
            f"Salesforce Contact creation failed — cannot run dependent tests. "
            f"Error: {data.get('error_message') or 'no record ID returned'}"
        )
    return record_id


@pytest.fixture(scope="session")
def real_sf_lead_id(api_server_url: str) -> str:
    """Create a Salesforce Lead once per session for read and update tests.

    The Lead is left in Salesforce after the session (manual cleanup needed).
    """
    return _create_lead(api_server_url, f"IntegLead{_rnd()}", f"Corp{_rnd()}")


@pytest.fixture(scope="session")
def real_sf_contact_id(api_server_url: str) -> str:
    """Create a Salesforce Contact once per session for read and update tests.

    The Contact is left in Salesforce after the session (manual cleanup needed).
    """
    return _create_contact(api_server_url, f"IntegContact{_rnd()}")


@pytest.fixture
def deletable_lead_id(api_server_url: str) -> str:
    """Create a fresh Salesforce Lead per test for delete tests.

    Each invocation creates a new record so the delete test always operates
    on an existing record.
    """
    return _create_lead(api_server_url, f"DelLead{_rnd()}", f"Corp{_rnd()}")


@pytest.fixture
def deletable_contact_id(api_server_url: str) -> str:
    """Create a fresh Salesforce Contact per test for delete tests."""
    return _create_contact(api_server_url, f"DelContact{_rnd()}")
