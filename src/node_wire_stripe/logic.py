from __future__ import annotations

import asyncio
import logging
from typing import Any

import stripe

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.models import ErrorCategory

from .schema import (
    CancelSubscriptionInput,
    ChargeInput,
    CreatePaymentIntentInput,
    CreateSubscriptionInput,
    IssueRefundInput,
    StripeOperationOutput,
)

logger = logging.getLogger("connectors.stripe")


class StripeConnector(BaseConnector):
    """Stripe connector: payments and subscriptions as @nw_action methods."""

    connector_id = "stripe"
    output_model = StripeOperationOutput

    error_map = {
        stripe.error.RateLimitError: (ErrorCategory.RETRYABLE, "STRIPE_RATE_LIMIT"),
        stripe.error.APIConnectionError: (ErrorCategory.RETRYABLE, "STRIPE_API_CONNECTION"),
        stripe.error.CardError: (ErrorCategory.BUSINESS, "STRIPE_CARD_ERROR"),
        stripe.error.InvalidRequestError: (ErrorCategory.BUSINESS, "STRIPE_INVALID_REQUEST"),
        stripe.error.AuthenticationError: (ErrorCategory.AUTH, "STRIPE_AUTH_ERROR"),
        stripe.error.StripeError: (ErrorCategory.FATAL, "STRIPE_ERROR"),
    }

    def _get_api_key(self) -> str:
        return self.secret_provider.get_secret("stripe_api_key")

    @nw_action("charge")
    async def charge(self, params: ChargeInput, *, trace_id: str) -> StripeOperationOutput:
        api_key = self._get_api_key()

        logger.info(
            "Creating Stripe charge",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "charge",
                "amount": params.amount,
                "currency": params.currency,
            },
        )

        def _create() -> stripe.Charge:
            return stripe.Charge.create(
                api_key=api_key,
                amount=params.amount,
                currency=params.currency,
                source=params.source,
                description=params.description,
            )

        try:
            charge = await asyncio.to_thread(_create)
        except Exception as exc:
            logger.error(
                "Stripe charge creation failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "charge",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            charge_id=charge.get("id"),
            receipt_url=charge.get("receipt_url"),
            status="succeeded" if charge.get("paid") else "failed",
        )

    @nw_action("create_payment_intent")
    async def create_payment_intent(
        self, params: CreatePaymentIntentInput, *, trace_id: str
    ) -> StripeOperationOutput:
        api_key = self._get_api_key()

        logger.info(
            "Creating Stripe Payment Intent",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "create_payment_intent",
                "amount": params.amount,
                "currency": params.currency,
            },
        )

        def _create() -> stripe.PaymentIntent:
            return stripe.PaymentIntent.create(
                api_key=api_key,
                amount=params.amount,
                currency=params.currency,
                customer=params.customer_id,
                payment_method=params.payment_method,
                confirm=params.confirm,
                description=params.description,
                metadata=params.metadata,
            )

        try:
            pi = await asyncio.to_thread(_create)
        except Exception as exc:
            logger.error(
                "Stripe Payment Intent creation failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "create_payment_intent",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            payment_intent_id=pi.get("id"),
            client_secret=pi.get("client_secret"),
            status=pi.get("status"),
        )

    @nw_action("create_subscription")
    async def create_subscription(self, params: CreateSubscriptionInput, *, trace_id: str) -> StripeOperationOutput:
        api_key = self._get_api_key()

        logger.info(
            "Creating Stripe Subscription",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "create_subscription",
                "customer_id": params.customer_id,
                "price_id": params.price_id,
            },
        )

        def _create() -> stripe.Subscription:
            payment_method_id = params.default_payment_method

            # If card_token is provided, create and attach PaymentMethod
            if params.card_token:
                pm = stripe.PaymentMethod.create(
                    api_key=api_key,
                    type="card",
                    card={"token": params.card_token},
                )
                stripe.PaymentMethod.attach(
                    pm.id,
                    api_key=api_key,
                    customer=params.customer_id,
                )
                payment_method_id = pm.id

            return stripe.Subscription.create(
                api_key=api_key,
                customer=params.customer_id,
                items=[{"price": params.price_id}],
                payment_behavior=params.payment_behavior,
                default_payment_method=payment_method_id,
                metadata=params.metadata,
            )

        try:
            sub = await asyncio.to_thread(_create)
        except Exception as exc:
            logger.error(
                "Stripe Subscription creation failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "create_subscription",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        # Subscription might have a setup_intent or latest_invoice.payment_intent
        client_secret = None
        if sub.get("pending_setup_intent"):
            si = stripe.SetupIntent.retrieve(sub.get("pending_setup_intent"), api_key=api_key)
            client_secret = si.get("client_secret")
        elif sub.get("latest_invoice"):
            inv = stripe.Invoice.retrieve(sub.get("latest_invoice"), api_key=api_key)
            if inv.get("payment_intent"):
                pi = stripe.PaymentIntent.retrieve(inv.get("payment_intent"), api_key=api_key)
                client_secret = pi.get("client_secret")

        return StripeOperationOutput(
            subscription_id=sub.get("id"),
            status=sub.get("status"),
            client_secret=client_secret,
        )

    @nw_action("cancel_subscription")
    async def cancel_subscription(self, params: CancelSubscriptionInput, *, trace_id: str) -> StripeOperationOutput:
        api_key = self._get_api_key()

        logger.info(
            "Cancelling Stripe Subscription",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "cancel_subscription",
                "subscription_id": params.subscription_id,
            },
        )

        def _cancel() -> stripe.Subscription:
            if params.cancel_at_period_end:
                return stripe.Subscription.modify(
                    params.subscription_id,
                    api_key=api_key,
                    cancel_at_period_end=True,
                )
            else:
                return stripe.Subscription.cancel(
                    params.subscription_id,
                    api_key=api_key,
                )

        try:
            sub = await asyncio.to_thread(_cancel)
        except Exception as exc:
            logger.error(
                "Stripe Subscription cancellation failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "cancel_subscription",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            subscription_id=sub.get("id"),
            status=sub.get("status"),
        )

    @nw_action("issue_refund")
    async def issue_refund(self, params: IssueRefundInput, *, trace_id: str) -> StripeOperationOutput:
        api_key = self._get_api_key()

        logger.info(
            "Issuing Stripe Refund",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "issue_refund",
                "charge_id": params.charge_id,
                "payment_intent_id": params.payment_intent_id,
            },
        )

        def _refund() -> stripe.Refund:
            return stripe.Refund.create(
                api_key=api_key,
                charge=params.charge_id,
                payment_intent=params.payment_intent_id,
                amount=params.amount,
                reason=params.reason,
                metadata=params.metadata,
            )

        try:
            refund = await asyncio.to_thread(_refund)
        except Exception as exc:
            logger.error(
                "Stripe Refund issuance failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "issue_refund",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            refund_id=refund.get("id"),
            status=refund.get("status"),
        )
