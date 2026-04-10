from __future__ import annotations

import asyncio
import logging

import stripe

from node_wire_runtime import BaseConnector, nw_action
from node_wire_runtime.models import ErrorCategory

from .schema import ChargeInput, ChargeOutput

logger = logging.getLogger("connectors.stripe")


class StripeConnector(BaseConnector):
    """Stripe connector: charges and future SDK operations as @nw_action methods."""

    connector_id = "stripe"
    action = "charge"
    output_model = ChargeOutput

    error_map = {
        stripe.error.RateLimitError: (ErrorCategory.RETRYABLE, "STRIPE_RATE_LIMIT"),
        stripe.error.APIConnectionError: (ErrorCategory.RETRYABLE, "STRIPE_API_CONNECTION"),
        stripe.error.CardError: (ErrorCategory.BUSINESS, "STRIPE_CARD_ERROR"),
        stripe.error.InvalidRequestError: (ErrorCategory.BUSINESS, "STRIPE_INVALID_REQUEST"),
        stripe.error.AuthenticationError: (ErrorCategory.AUTH, "STRIPE_AUTH_ERROR"),
        stripe.error.StripeError: (ErrorCategory.FATAL, "STRIPE_ERROR"),
    }

    @nw_action("charge")
    async def charge(self, params: ChargeInput, *, trace_id: str) -> ChargeOutput:
        # The factory injects a StaticTokenAuthProvider with the Stripe API key.
        # We extract the raw key from the Authorization header value.
        auth_headers = await self.get_auth_headers()
        raw_auth = auth_headers.get("Authorization", "")
        # Strip any prefix (e.g. "Bearer ") — Stripe expects the raw key.
        api_key = raw_auth.split(" ", 1)[-1].strip() if raw_auth else ""
        if not api_key:
            # Backward-compatibility fallback: read directly from secret_provider.
            api_key = self.secret_provider.get_secret("stripe_api_key")

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
            stripe.api_key = api_key
            return stripe.Charge.create(
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
                    "amount": params.amount,
                    "currency": params.currency,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        logger.info(
            "Stripe charge created successfully",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "charge",
                "charge_id": charge.get("id"),
            },
        )

        return ChargeOutput(
            charge_id=charge.get("id"),
            receipt_url=charge.get("receipt_url"),
        )
