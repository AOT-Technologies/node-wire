from __future__ import annotations

import stripe

from node_wire_runtime import ErrorCategory, ErrorMapper


# Stripe SDK error mappings following the spec examples.
ErrorMapper.register(stripe.error.RateLimitError, ErrorCategory.RETRYABLE, code="STRIPE_RATE_LIMIT")
ErrorMapper.register(stripe.error.CardError, ErrorCategory.BUSINESS, code="STRIPE_CARD_ERROR")
ErrorMapper.register(stripe.error.AuthenticationError, ErrorCategory.AUTH, code="STRIPE_AUTH_ERROR")
ErrorMapper.register(stripe.error.APIConnectionError, ErrorCategory.RETRYABLE, code="STRIPE_API_CONNECTION")
ErrorMapper.register(stripe.error.StripeError, ErrorCategory.FATAL, code="STRIPE_ERROR")

