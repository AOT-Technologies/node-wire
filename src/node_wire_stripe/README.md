<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Node Wire connector — Stripe

The Stripe connector provides a reliable, async adapter for processing payments and managing subscriptions using the Stripe Python SDK. It follows the Node Wire platform contract: consistent error handling, resilience (retries/circuit breaking), and standardized telemetry.

## Supported actions

The connector exposes several actions through the `@nw_action` decorator. Each action is available via REST, gRPC, and MCP.

| Action | Description | Key Parameters |
| :--- | :--- | :--- |
| `charge` | Legacy charge creation. | `amount`, `currency`, `source` |
| `create_payment_intent` | Modern payment flow for one-time payments. | `amount`, `currency`, `customer_id`, `confirm` |
| `create_subscription` | Create a recurring subscription. | `customer_id`, `price_id`, `card_token` |
| `cancel_subscription` | Terminate or schedule the end of a subscription. | `subscription_id`, `cancel_at_period_end` |
| `issue_refund` | Full or partial refund for a charge or payment intent. | `charge_id` or `payment_intent_id`, `amount` |

## Setup & configuration

### Environment variables

The connector requires a Stripe secret API key. By default, the `EnvSecretProvider` looks for:

- `STRIPE_API_KEY`: Your Stripe secret key (for example, `sk_test_...` or `sk_live_...`).

Add this to your `.env` or system environment:

```bash
STRIPE_API_KEY=<your-stripe-secret-key>
```

Replace `<your-stripe-secret-key>` with your Stripe secret key.

### Enabling the connector

In `config/connectors.yaml`, make sure the connector is enabled and exposed:

```yaml
connectors:
  stripe:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
```

## Detailed action reference

### `create_subscription`

This action supports multiple payment integration flows:

- **Saved payment method**: Pass `default_payment_method` with an existing `pm_xxx` ID.
- **Card token**: Pass `card_token` (for example, `tok_visa`). The connector automatically creates a PaymentMethod and attach it to the customer before creating the subscription.
- **SCA / action required**: If the subscription requires further action (like 3D Secure), the connector returns the `client_secret` from the associated Setup Intent or Payment Intent.

### `cancel_subscription`

- Set `cancel_at_period_end: true` to let the subscription finish its current cycle.
- Set `cancel_at_period_end: false` (default) to terminate the subscription immediately.

## Error handling

The connector maps Stripe exceptions to Node Wire error categories:

- `RateLimitError` -> `RETRYABLE` (`STRIPE_RATE_LIMIT`)
- `CardError` -> `BUSINESS` (`STRIPE_CARD_ERROR`)
- `AuthenticationError` -> `AUTH` (`STRIPE_AUTH_ERROR`)
- `APIConnectionError` -> `RETRYABLE` (`STRIPE_API_CONNECTION`)
- `InvalidRequestError` -> `BUSINESS` (`STRIPE_INVALID_REQUEST`)
- `StripeError` -> `FATAL` (`STRIPE_ERROR`)

Trace IDs are included in all error responses for easier troubleshooting in the Stripe Dashboard.
