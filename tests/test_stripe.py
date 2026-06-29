#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from node_wire_runtime import SecretProvider
from node_wire_stripe.logic import StripeConnector
from node_wire_stripe.schema import (
    CancelSubscriptionInput,
    ChargeInput,
    CreatePaymentIntentInput,
    CreateSubscriptionInput,
    IssueRefundInput,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {
            "stripe_api_key": "sk_test_mock",
        }[key]


def _connector() -> StripeConnector:
    """Return a StripeConnector with mock secrets."""
    return StripeConnector(secret_provider=MockSecretProvider())


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_charge_happy_path():
    connector = _connector()
    params = ChargeInput(amount=1000, currency="usd", source="tok_visa")

    mock_charge = MagicMock(id="ch_123", receipt_url="http://stripe.com/receipt", paid=True)

    with patch("stripe.Charge.create", return_value=mock_charge) as mock_create:
        result = await connector.charge(params, trace_id="test-trace")

    assert result.charge_id == "ch_123"
    assert result.receipt_url == "http://stripe.com/receipt"
    assert result.status == "succeeded"
    mock_create.assert_called_once_with(
        api_key="sk_test_mock",
        amount=1000,
        currency="usd",
        source="tok_visa",
        customer=None,
        description=None,
        metadata=None,
        idempotency_key="test-trace",
    )


# ---------------------------------------------------------------------------
# Create Payment Intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_create_payment_intent_happy_path():
    connector = _connector()
    params = CreatePaymentIntentInput(amount=2000, currency="eur", confirm=True)

    mock_pi = MagicMock(id="pi_123", client_secret="secret_abc", status="requires_payment_method")

    with patch("stripe.PaymentIntent.create", return_value=mock_pi) as mock_create:
        result = await connector.create_payment_intent(params, trace_id="test-trace")

    assert result.payment_intent_id == "pi_123"
    assert result.client_secret == "secret_abc"
    assert result.status == "requires_payment_method"
    mock_create.assert_called_once_with(
        api_key="sk_test_mock",
        amount=2000,
        currency="eur",
        customer=None,
        payment_method=None,
        confirm=True,
        description=None,
        metadata=None,
        idempotency_key="test-trace",
    )


# ---------------------------------------------------------------------------
# Create Subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_create_subscription_with_card_token():
    connector = _connector()
    params = CreateSubscriptionInput(
        customer_id="cus_123", price_id="price_abc", card_token="tok_visa"
    )

    mock_pm = MagicMock(id="pm_123")
    mock_sub = MagicMock(
        id="sub_123", status="active", pending_setup_intent=None, latest_invoice=None
    )

    with (
        patch("stripe.PaymentMethod.create", return_value=mock_pm) as mock_pm_create,
        patch("stripe.PaymentMethod.attach") as mock_pm_attach,
        patch("stripe.Subscription.create", return_value=mock_sub) as mock_sub_create,
    ):
        result = await connector.create_subscription(params, trace_id="test-trace")

    assert result.subscription_id == "sub_123"
    assert result.status == "active"

    mock_pm_create.assert_called_once()
    mock_pm_attach.assert_called_once_with("pm_123", api_key="sk_test_mock", customer="cus_123")
    mock_sub_create.assert_called_once()
    assert mock_sub_create.call_args.kwargs["default_payment_method"] == "pm_123"
    assert mock_sub_create.call_args.kwargs["idempotency_key"] == "test-trace"


# ---------------------------------------------------------------------------
# Cancel Subscription
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_cancel_subscription_immediate():
    connector = _connector()
    params = CancelSubscriptionInput(subscription_id="sub_123", cancel_at_period_end=False)

    mock_sub = MagicMock(id="sub_123", status="canceled")

    with patch("stripe.Subscription.cancel", return_value=mock_sub) as mock_cancel:
        result = await connector.cancel_subscription(params, trace_id="test-trace")

    assert result.subscription_id == "sub_123"
    assert result.status == "canceled"
    mock_cancel.assert_called_once_with(
        "sub_123", api_key="sk_test_mock", idempotency_key="test-trace"
    )


# ---------------------------------------------------------------------------
# Issue Refund
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_issue_refund_happy_path():
    connector = _connector()
    params = IssueRefundInput(payment_intent_id="pi_123", amount=500)

    mock_refund = MagicMock(id="re_123", status="succeeded")

    with patch("stripe.Refund.create", return_value=mock_refund) as mock_refund_create:
        result = await connector.issue_refund(params, trace_id="test-trace")

    assert result.refund_id == "re_123"
    assert result.status == "succeeded"
    mock_refund_create.assert_called_once_with(
        api_key="sk_test_mock",
        charge=None,
        payment_intent="pi_123",
        amount=500,
        reason=None,
        metadata=None,
        idempotency_key="test-trace",
    )


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------


def test_stripe_schema_validation_bounds():
    """Verify that amount and currency bounds are enforced."""
    # Valid
    ChargeInput(amount=1, currency="usd", source="tok_visa")

    # Invalid amount (too small)
    with pytest.raises(ValidationError):
        ChargeInput(amount=0, currency="usd", source="tok_visa")

    # Invalid currency (wrong length/format)
    with pytest.raises(ValidationError):
        ChargeInput(amount=100, currency="us", source="tok_visa")

    with pytest.raises(ValidationError):
        ChargeInput(amount=100, currency="USDT", source="tok_visa")


# ---------------------------------------------------------------------------
# Error Mapping
# ---------------------------------------------------------------------------


def test_stripe_error_mapping():
    """Verify that Stripe exceptions are correctly mapped to ErrorCategory."""
    import stripe

    connector = _connector()
    from node_wire_runtime.models import ErrorCategory

    # Check specific mappings from StripeConnector.error_map
    assert connector.error_map[stripe.error.CardError] == (
        ErrorCategory.BUSINESS,
        "STRIPE_CARD_ERROR",
    )
    assert connector.error_map[stripe.error.RateLimitError] == (
        ErrorCategory.RETRYABLE,
        "STRIPE_RATE_LIMIT",
    )
    assert connector.error_map[stripe.error.AuthenticationError] == (
        ErrorCategory.AUTH,
        "STRIPE_AUTH_ERROR",
    )
