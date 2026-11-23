#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Simplified: the Stripe connector is OpenAPI-generated (RestConnector), so these tests drive
# its actions through a fake shared httpx client instead of patching the stripe SDK.
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

import node_wire_runtime.rest as rest
from node_wire_runtime import SecretProvider
from node_wire_runtime.models import ErrorCategory
from node_wire_stripe.logic import StripeConnector
from node_wire_stripe.schema import (
    DeleteSubscriptionsSubscriptionExposedIdInput,
    PostChargesInput,
    PostPaymentIntentsInput,
    PostRefundsInput,
    PostSubscriptionsInput,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class MockSecretProvider(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"STRIPE_BASIC_AUTH": "sk_test_mock:"}[key]


class _Resp:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.text = self.content.decode()
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.stripe.com")
            raise httpx.HTTPStatusError(
                "error", request=request, response=httpx.Response(self.status_code, request=request)
            )


class _Client:
    def __init__(self, response: _Resp | None = None, error: Exception | None = None) -> None:
        self.response, self.error = response, error
        self.calls: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _connector() -> StripeConnector:
    """Return a StripeConnector with mock secrets."""
    return StripeConnector(secret_provider=MockSecretProvider())


def _fake(monkeypatch: pytest.MonkeyPatch, client: _Client) -> _Client:
    monkeypatch.setattr(rest, "_shared_client", lambda _trust_env: client)
    monkeypatch.setattr(rest, "assert_safe_destination", AsyncMock(return_value=None))
    return client


def _form(call: dict[str, Any]) -> dict[str, Any]:
    return call["data"]


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_charge_happy_path(monkeypatch):
    client = _fake(monkeypatch, _Client(_Resp(200, {"id": "ch_123", "paid": True})))
    params = PostChargesInput(amount=1000, currency="usd", source="tok_visa")

    result = await _connector().post_charges(params, trace_id="test-trace")

    assert result.model_dump()["id"] == "ch_123"
    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.stripe.com/v1/charges"
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert _form(call) == {"amount": "1000", "currency": "usd", "source": "tok_visa"}


@pytest.mark.asyncio
async def test_stripe_charge_unpaid_is_returned_as_is(monkeypatch):
    _fake(monkeypatch, _Client(_Resp(200, {"id": "ch_456", "paid": False})))
    params = PostChargesInput(amount=1000, currency="usd", source="tok_visa")

    result = await _connector().post_charges(params, trace_id="test-trace")

    assert result.model_dump()["id"] == "ch_456"
    assert result.model_dump()["paid"] is False


# ---------------------------------------------------------------------------
# Payment intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_create_payment_intent_happy_path(monkeypatch):
    client = _fake(
        monkeypatch,
        _Client(
            _Resp(
                200,
                {
                    "id": "pi_123",
                    "client_secret": "secret_abc",
                    "status": "requires_payment_method",
                },
            )
        ),
    )
    params = PostPaymentIntentsInput(amount=2000, currency="eur", confirm=True)

    result = await _connector().post_payment_intents(params, trace_id="test-trace")

    data = result.model_dump()
    assert data["id"] == "pi_123"
    assert data["client_secret"] == "secret_abc"
    assert client.calls[0]["url"] == "https://api.stripe.com/v1/payment_intents"
    form = _form(client.calls[0])
    assert (form["amount"], form["currency"]) == ("2000", "eur")
    assert str(form["confirm"]).lower() == "true"


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_create_subscription(monkeypatch):
    client = _fake(monkeypatch, _Client(_Resp(200, {"id": "sub_123", "status": "active"})))
    params = PostSubscriptionsInput(customer="cus_123", items=[{"price": "price_abc"}])

    result = await _connector().post_subscriptions(params, trace_id="test-trace")

    assert result.model_dump()["id"] == "sub_123"
    assert client.calls[0]["url"] == "https://api.stripe.com/v1/subscriptions"
    form = _form(client.calls[0])
    assert form["customer"] == "cus_123"
    assert "price_abc" in str(form)


@pytest.mark.asyncio
async def test_stripe_cancel_subscription_is_a_delete_on_the_subscription_path(monkeypatch):
    client = _fake(monkeypatch, _Client(_Resp(200, {"id": "sub_123", "status": "canceled"})))
    params = DeleteSubscriptionsSubscriptionExposedIdInput(subscription_exposed_id="sub_123")

    result = await _connector().delete_subscriptions_subscription_exposed_id(
        params, trace_id="test-trace"
    )

    assert result.model_dump()["status"] == "canceled"
    assert client.calls[0]["method"] == "DELETE"
    assert client.calls[0]["url"] == "https://api.stripe.com/v1/subscriptions/sub_123"


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_issue_refund_happy_path(monkeypatch):
    client = _fake(monkeypatch, _Client(_Resp(200, {"id": "re_123", "status": "succeeded"})))
    params = PostRefundsInput(payment_intent="pi_123", amount=500)

    result = await _connector().post_refunds(params, trace_id="test-trace")

    assert result.model_dump()["id"] == "re_123"
    assert client.calls[0]["url"] == "https://api.stripe.com/v1/refunds"
    assert _form(client.calls[0]) == {"payment_intent": "pi_123", "amount": "500"}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_stripe_schema_validation():
    """Generated inputs reject unknown fields and enforce required path parameters."""
    PostChargesInput(amount=1, currency="usd", source="tok_visa")

    with pytest.raises(ValidationError):
        PostChargesInput(amount=1, currency="usd", not_a_stripe_field="x")

    with pytest.raises(ValidationError):
        PostChargesInput(amount="lots", currency="usd")

    with pytest.raises(ValidationError):
        DeleteSubscriptionsSubscriptionExposedIdInput()


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_stripe_error_mapping():
    """HTTP failures map to the runtime error categories."""
    error_map = _connector().error_map

    assert error_map[httpx.HTTPStatusError] == (ErrorCategory.BUSINESS, "HTTP_STATUS_ERROR")
    assert error_map[httpx.ConnectError] == (ErrorCategory.RETRYABLE, "HTTP_CONNECT_ERROR")
    assert error_map[httpx.TimeoutException] == (ErrorCategory.RETRYABLE, "HTTP_TIMEOUT")


# ---------------------------------------------------------------------------
# Runtime error mapping via run()
# ---------------------------------------------------------------------------

_CHARGE = {"action": "post_charges", "amount": 100, "currency": "usd", "source": "tok_visa"}


@pytest.mark.asyncio
async def test_stripe_run_maps_card_declined_to_business(monkeypatch):
    _fake(monkeypatch, _Client(_Resp(402, {"error": {"code": "card_declined"}})))

    result = await _connector().run(dict(_CHARGE))

    assert result.success is False
    assert result.error_category == ErrorCategory.BUSINESS
    assert result.error_code == "HTTP_STATUS_ERROR"


@pytest.mark.asyncio
async def test_stripe_run_maps_connect_error_to_retryable(monkeypatch):
    _fake(monkeypatch, _Client(error=httpx.ConnectError("down")))

    result = await _connector().run(dict(_CHARGE))

    assert result.success is False
    assert result.error_category == ErrorCategory.RETRYABLE
    assert result.error_code == "HTTP_CONNECT_ERROR"


@pytest.mark.asyncio
async def test_stripe_run_maps_timeout_to_retryable(monkeypatch):
    _fake(monkeypatch, _Client(error=httpx.ReadTimeout("slow")))

    result = await _connector().run(dict(_CHARGE))

    assert result.success is False
    assert result.error_category == ErrorCategory.RETRYABLE


# ---------------------------------------------------------------------------
# Failures propagate from the action methods
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_action_failure_raises(monkeypatch):
    _fake(monkeypatch, _Client(error=httpx.ConnectError("stripe down")))
    params = PostPaymentIntentsInput(amount=1000, currency="usd")

    with pytest.raises(httpx.ConnectError, match="stripe down"):
        await _connector().post_payment_intents(params, trace_id="test-trace")


@pytest.mark.asyncio
async def test_stripe_refund_http_error_raises(monkeypatch):
    _fake(monkeypatch, _Client(_Resp(400, {"error": {"message": "bad"}})))
    params = PostRefundsInput(payment_intent="pi_123")

    with pytest.raises(httpx.HTTPStatusError):
        await _connector().post_refunds(params, trace_id="test-trace")
