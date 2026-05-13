# Node Wire Connector — Stripe

The Stripe connector provides a reliable, async adapter for processing payments and managing subscriptions using the Stripe Python SDK. It follows the Node Wire platform contract: consistent error handling, resilience (retries/circuit breaking), and standardized telemetry.

## Supported Actions

The connector exposes several actions through the `@nw_action` decorator. Each action is available via REST, gRPC, and MCP.

| Action | Description | Key Parameters |
| :--- | :--- | :--- |
| `charge` | Legacy charge creation. | `amount`, `currency`, `source` |
| `create_payment_intent` | Modern payment flow for one-time payments. | `amount`, `currency`, `customer_id`, `confirm` |
| `create_subscription` | Create a recurring subscription. | `customer_id`, `price_id`, `card_token` |
| `cancel_subscription` | Terminate or schedule the end of a subscription. | `subscription_id`, `cancel_at_period_end` |
| `issue_refund` | Full or partial refund for a charge or payment intent. | `charge_id` or `payment_intent_id`, `amount` |

## Setup & Configuration

### Environment Variables

The connector requires a Stripe secret API key. By default, the `EnvSecretProvider` looks for:

- `STRIPE_API_KEY`: Your Stripe secret key (e.g., `sk_test_...` or `sk_live_...`).

Add this to your `.env` or system environment:

```bash
STRIPE_API_KEY=sk_test_your_secret_key
```

### Enabling the Connector

In `config/connectors.yaml`, ensuring the connector is enabled and exposed:

```yaml
connectors:
  stripe:
    enabled: true
    exposed_via: ["rest", "grpc", "mcp"]
```

## Detailed Action Reference

### `create_subscription`

This action supports multiple payment integration flows:

1.  **Saved Payment Method**: Pass `default_payment_method` with an existing `pm_xxx` ID.
2.  **Card Token**: Pass `card_token` (e.g., `tok_visa`). The connector will automatically create a PaymentMethod and attach it to the customer before creating the subscription.
3.  **SCA / Action Required**: If the subscription requires further action (like 3D Secure), the connector returns the `client_secret` from the associated Setup Intent or Payment Intent.

### `cancel_subscription`

- Set `cancel_at_period_end: true` to let the subscription finish its current cycle.
- Set `cancel_at_period_end: false` (default) to terminate the subscription immediately.

## Error Handling

Mapped Stripe exceptions to Node Wire error categories:

- `RateLimitError` -> `RETRYABLE` (`STRIPE_RATE_LIMIT`)
- `CardError` -> `BUSINESS` (`STRIPE_CARD_ERROR`)
- `AuthenticationError` -> `AUTH` (`STRIPE_AUTH_ERROR`)
- `APIConnectionError` -> `RETRYABLE` (`STRIPE_API_CONNECTION`)
- `InvalidRequestError` -> `BUSINESS` (`STRIPE_INVALID_REQUEST`)
- `StripeError` -> `FATAL` (`STRIPE_ERROR`)

Trace IDs are included in all error responses for easier troubleshooting in the Stripe Dashboard.
