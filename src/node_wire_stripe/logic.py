#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, cast

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

    error_map: ClassVar[dict[type[BaseException], tuple[ErrorCategory, str]]] = {
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
            create = cast(Any, stripe.Charge.create)
            return create(
                api_key=api_key,
                amount=params.amount,
                currency=params.currency,
                source=params.source,
                customer=params.customer_id,
                description=params.description,
                metadata=params.metadata,
                idempotency_key=params.idempotency_key or trace_id,
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
            charge_id=getattr(charge, "id", None),
            receipt_url=getattr(charge, "receipt_url", None),
            status="succeeded" if getattr(charge, "paid", False) else "failed",
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
            create = cast(Any, stripe.PaymentIntent.create)
            return create(
                api_key=api_key,
                amount=params.amount,
                currency=params.currency,
                customer=params.customer_id,
                payment_method=params.payment_method,
                confirm=params.confirm,
                description=params.description,
                metadata=params.metadata,
                idempotency_key=params.idempotency_key or trace_id,
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
                    "error_message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            payment_intent_id=getattr(pi, "id", None),
            client_secret=getattr(pi, "client_secret", None),
            status=getattr(pi, "status", None),
        )

    @nw_action("create_subscription")
    async def create_subscription(
        self, params: CreateSubscriptionInput, *, trace_id: str
    ) -> StripeOperationOutput:
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

            return cast(Any, stripe.Subscription.create)(
                api_key=api_key,
                customer=params.customer_id,
                items=[{"price": params.price_id}],
                payment_behavior=params.payment_behavior,
                default_payment_method=payment_method_id,
                metadata=params.metadata,
                idempotency_key=params.idempotency_key or trace_id,
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
                    "error_message": str(exc),
                },
            )
            raise

        # Subscription might have a setup_intent or latest_invoice.payment_intent
        client_secret = None
        pending_setup_intent = getattr(sub, "pending_setup_intent", None)
        latest_invoice_id = getattr(sub, "latest_invoice", None)

        def _stripe_obj_id(obj: Any) -> str:
            if isinstance(obj, str):
                return obj
            oid = getattr(obj, "id", None)
            return str(oid) if oid is not None else str(obj)

        if pending_setup_intent:
            si = await asyncio.to_thread(
                stripe.SetupIntent.retrieve,
                _stripe_obj_id(pending_setup_intent),
                api_key=api_key,
            )
            client_secret = getattr(si, "client_secret", None)
        elif latest_invoice_id:
            inv = await asyncio.to_thread(
                stripe.Invoice.retrieve,
                _stripe_obj_id(latest_invoice_id),
                api_key=api_key,
            )
            pi_id = getattr(inv, "payment_intent", None)
            if pi_id:
                pi = await asyncio.to_thread(
                    stripe.PaymentIntent.retrieve,
                    _stripe_obj_id(pi_id),
                    api_key=api_key,
                )
                client_secret = getattr(pi, "client_secret", None)

        return StripeOperationOutput(
            subscription_id=getattr(sub, "id", None),
            status=getattr(sub, "status", None),
            client_secret=client_secret,
        )

    @nw_action("cancel_subscription")
    async def cancel_subscription(
        self, params: CancelSubscriptionInput, *, trace_id: str
    ) -> StripeOperationOutput:
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
                    idempotency_key=params.idempotency_key or trace_id,
                )
            else:
                return stripe.Subscription.cancel(
                    params.subscription_id,
                    api_key=api_key,
                    idempotency_key=params.idempotency_key or trace_id,
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
                    "error_message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            subscription_id=getattr(sub, "id", None),
            status=getattr(sub, "status", None),
        )

    @nw_action("issue_refund")
    async def issue_refund(
        self, params: IssueRefundInput, *, trace_id: str
    ) -> StripeOperationOutput:
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
            create = cast(Any, stripe.Refund.create)
            return create(
                api_key=api_key,
                charge=params.charge_id,
                payment_intent=params.payment_intent_id,
                amount=params.amount,
                reason=params.reason,
                metadata=params.metadata,
                idempotency_key=params.idempotency_key or trace_id,
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
                    "error_message": str(exc),
                },
            )
            raise

        return StripeOperationOutput(
            refund_id=getattr(refund, "id", None),
            status=getattr(refund, "status", None),
        )
