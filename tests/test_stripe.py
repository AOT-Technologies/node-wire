#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import stripe
from pydantic import ValidationError

from node_wire_runtime import SecretProvider
from node_wire_runtime.models import ErrorCategory
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


@pytest.mark.asyncio
async def test_stripe_charge_unpaid_returns_failed_status():
    connector = _connector()
    params = ChargeInput(amount=1000, currency="usd", source="tok_visa")

    mock_charge = MagicMock(id="ch_456", receipt_url=None, paid=False)

    with patch("stripe.Charge.create", return_value=mock_charge):
        result = await connector.charge(params, trace_id="test-trace")

    assert result.charge_id == "ch_456"
    assert result.status == "failed"


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


@pytest.mark.asyncio
async def test_stripe_create_subscription_with_default_payment_method():
    connector = _connector()
    params = CreateSubscriptionInput(
        customer_id="cus_123",
        price_id="price_abc",
        default_payment_method="pm_existing",
    )

    mock_sub = MagicMock(
        id="sub_456", status="active", pending_setup_intent=None, latest_invoice=None
    )

    with patch("stripe.Subscription.create", return_value=mock_sub) as mock_sub_create:
        result = await connector.create_subscription(params, trace_id="test-trace")

    assert result.subscription_id == "sub_456"
    assert mock_sub_create.call_args.kwargs["default_payment_method"] == "pm_existing"


@pytest.mark.asyncio
async def test_stripe_create_subscription_pending_setup_intent_client_secret():
    connector = _connector()
    params = CreateSubscriptionInput(customer_id="cus_123", price_id="price_abc")

    mock_sub = MagicMock(
        id="sub_si",
        status="incomplete",
        pending_setup_intent="seti_abc",
        latest_invoice=None,
    )
    mock_si = MagicMock(client_secret="seti_secret_xyz")

    with (
        patch("stripe.Subscription.create", return_value=mock_sub),
        patch("stripe.SetupIntent.retrieve", return_value=mock_si) as mock_si_retrieve,
    ):
        result = await connector.create_subscription(params, trace_id="test-trace")

    assert result.client_secret == "seti_secret_xyz"
    mock_si_retrieve.assert_called_once_with("seti_abc", api_key="sk_test_mock")


@pytest.mark.asyncio
async def test_stripe_create_subscription_latest_invoice_payment_intent_client_secret():
    connector = _connector()
    params = CreateSubscriptionInput(customer_id="cus_123", price_id="price_abc")

    mock_sub = MagicMock(
        id="sub_inv",
        status="incomplete",
        pending_setup_intent=None,
        latest_invoice="in_abc",
    )
    mock_inv = MagicMock(payment_intent="pi_abc")
    mock_pi = MagicMock(client_secret="pi_secret_xyz")

    with (
        patch("stripe.Subscription.create", return_value=mock_sub),
        patch("stripe.Invoice.retrieve", return_value=mock_inv) as mock_inv_retrieve,
        patch("stripe.PaymentIntent.retrieve", return_value=mock_pi) as mock_pi_retrieve,
    ):
        result = await connector.create_subscription(params, trace_id="test-trace")

    assert result.client_secret == "pi_secret_xyz"
    mock_inv_retrieve.assert_called_once_with("in_abc", api_key="sk_test_mock")
    mock_pi_retrieve.assert_called_once_with("pi_abc", api_key="sk_test_mock")


@pytest.mark.asyncio
async def test_stripe_create_subscription_setup_intent_object_id():
    """Cover _stripe_obj_id when pending_setup_intent is an object, not a string."""
    connector = _connector()
    params = CreateSubscriptionInput(customer_id="cus_123", price_id="price_abc")

    setup_intent_obj = MagicMock(id="seti_obj")
    mock_sub = MagicMock(
        id="sub_obj",
        status="incomplete",
        pending_setup_intent=setup_intent_obj,
        latest_invoice=None,
    )
    mock_si = MagicMock(client_secret="seti_obj_secret")

    with (
        patch("stripe.Subscription.create", return_value=mock_sub),
        patch("stripe.SetupIntent.retrieve", return_value=mock_si) as mock_si_retrieve,
    ):
        result = await connector.create_subscription(params, trace_id="test-trace")

    assert result.client_secret == "seti_obj_secret"
    mock_si_retrieve.assert_called_once_with("seti_obj", api_key="sk_test_mock")


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


@pytest.mark.asyncio
async def test_stripe_cancel_subscription_at_period_end():
    connector = _connector()
    params = CancelSubscriptionInput(subscription_id="sub_123", cancel_at_period_end=True)

    mock_sub = MagicMock(id="sub_123", status="active")

    with patch("stripe.Subscription.modify", return_value=mock_sub) as mock_modify:
        result = await connector.cancel_subscription(params, trace_id="test-trace")

    assert result.subscription_id == "sub_123"
    assert result.status == "active"
    mock_modify.assert_called_once_with(
        "sub_123",
        api_key="sk_test_mock",
        cancel_at_period_end=True,
        idempotency_key="test-trace",
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


# ---------------------------------------------------------------------------
# Runtime error mapping via run()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_run_maps_card_error_to_business():
    connector = _connector()
    card_error = stripe.error.CardError(
        message="Your card was declined.",
        param="number",
        code="card_declined",
        http_status=402,
    )

    with patch("stripe.Charge.create", side_effect=card_error):
        result = await connector.run(
            {"action": "charge", "amount": 100, "currency": "usd", "source": "tok_visa"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "STRIPE_CARD_ERROR"


@pytest.mark.asyncio
async def test_stripe_run_maps_rate_limit_to_retryable():
    connector = _connector()
    rate_error = stripe.error.RateLimitError(
        message="Rate limit",
        http_status=429,
        code="rate_limit",
    )

    with patch("stripe.Charge.create", side_effect=rate_error):
        result = await connector.run(
            {"action": "charge", "amount": 100, "currency": "usd", "source": "tok_visa"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.RETRYABLE
    assert result.error_code == "STRIPE_RATE_LIMIT"


@pytest.mark.asyncio
async def test_stripe_run_maps_authentication_error_to_auth():
    connector = _connector()
    auth_error = stripe.error.AuthenticationError(
        message="Invalid API Key",
        http_status=401,
        code="api_key_invalid",
    )

    with patch("stripe.Charge.create", side_effect=auth_error):
        result = await connector.run(
            {"action": "charge", "amount": 100, "currency": "usd", "source": "tok_visa"}
        )

    assert result.success is False
    assert result.error_category == ErrorCategory.AUTH
    assert result.error_code == "STRIPE_AUTH_ERROR"


# ---------------------------------------------------------------------------
# Exception logging branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_create_payment_intent_failure_logs_and_raises():
    connector = _connector()
    params = CreatePaymentIntentInput(amount=1000, currency="usd")

    with patch("stripe.PaymentIntent.create", side_effect=RuntimeError("stripe down")):
        with pytest.raises(RuntimeError, match="stripe down"):
            await connector.create_payment_intent(params, trace_id="test-trace")


@pytest.mark.asyncio
async def test_stripe_create_subscription_failure_logs_and_raises():
    connector = _connector()
    params = CreateSubscriptionInput(customer_id="cus_123", price_id="price_abc")

    with patch("stripe.Subscription.create", side_effect=RuntimeError("sub failed")):
        with pytest.raises(RuntimeError, match="sub failed"):
            await connector.create_subscription(params, trace_id="test-trace")


@pytest.mark.asyncio
async def test_stripe_cancel_subscription_failure_logs_and_raises():
    connector = _connector()
    params = CancelSubscriptionInput(subscription_id="sub_123")

    with patch("stripe.Subscription.cancel", side_effect=RuntimeError("cancel failed")):
        with pytest.raises(RuntimeError, match="cancel failed"):
            await connector.cancel_subscription(params, trace_id="test-trace")


@pytest.mark.asyncio
async def test_stripe_issue_refund_failure_logs_and_raises():
    connector = _connector()
    params = IssueRefundInput(payment_intent_id="pi_123")

    with patch("stripe.Refund.create", side_effect=RuntimeError("refund failed")):
        with pytest.raises(RuntimeError, match="refund failed"):
            await connector.issue_refund(params, trace_id="test-trace")
