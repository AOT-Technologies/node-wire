#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def real_stripe_charge_id(api_server_url: str) -> str:
    """Create a real Stripe test charge via the API and return its charge ID.

    Uses the default tok_visa source hardcoded in StripeChargeInput so no extra
    env vars are needed beyond STRIPE_API_KEY.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/stripe-charge",
            json={"amount": 500, "currency": "usd", "description": "nw-integration-test charge"},
        )
    resp.raise_for_status()
    data = resp.json()
    charge_id = data.get("final_resource_id")
    if not charge_id:
        pytest.skip(
            f"Stripe charge setup failed — cannot run refund tests. "
            f"Error: {data.get('error_message') or 'no charge_id returned'}"
        )
    return charge_id


@pytest.fixture(scope="session")
def real_stripe_subscription_id(api_server_url: str) -> str:
    """Create a real Stripe subscription and return its subscription ID.

    Requires STRIPE_TEST_CUSTOMER_ID and STRIPE_TEST_PRICE_ID env vars.
    Tests that use this fixture are skipped when the vars are absent.
    """
    customer_id = os.environ.get("STRIPE_TEST_CUSTOMER_ID")
    price_id = os.environ.get("STRIPE_TEST_PRICE_ID")
    if not customer_id or not price_id:
        pytest.skip(
            "STRIPE_TEST_CUSTOMER_ID and STRIPE_TEST_PRICE_ID are required for subscription tests"
        )

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/stripe-subscription",
            json={"customer_id": customer_id, "price_id": price_id},
        )
    resp.raise_for_status()
    data = resp.json()
    sub_id = data.get("final_resource_id")
    if not sub_id:
        pytest.skip(
            f"Stripe subscription setup failed — cannot run cancel tests. "
            f"Error: {data.get('error_message') or 'no subscription_id returned'}"
        )
    return sub_id
