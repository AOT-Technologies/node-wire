<!--
SPDX-FileCopyrightText: 2026 AOT Technologies

SPDX-License-Identifier: Apache-2.0
-->

# Stripe Playground Integration Tests

End-to-end Playwright tests that open the Playground UI in a real browser,
navigate to the Stripe connector panel, and assert on the rendered pipeline
state. No mocking — every test hits the real Stripe test-mode API.

## What is tested

| Test | Action |
|------|--------|
| `test_stripe_charge_default` | `charge` — default values (2000 usd) |
| `test_stripe_charge_custom_amount` | `charge` — custom amount + description |
| `test_stripe_charge_no_description` | `charge` — empty description |
| `test_stripe_payment_intent_default` | `payment_intent` — defaults (5000 usd, pm_card_visa) |
| `test_stripe_payment_intent_custom_amount` | `payment_intent` — custom amount, result tag contains pi_ |
| `test_stripe_payment_intent_no_payment_method` | `payment_intent` — no payment method |
| `test_stripe_cancel_subscription_invalid_id` | `cancel_subscription` — nonexistent ID, expects error state |
| `test_stripe_cancel_subscription` | `cancel_subscription` — real subscription ID (requires env vars) |
| `test_stripe_refund_by_charge_id` | `refund` — full refund against a real charge |
| `test_stripe_refund_invalid_id` | `refund` — nonexistent ID, expects error state |
| `test_stripe_switch_charge_then_payment_intent` | Cross-action switch on same page |

## How it works

The test session starts a real FastAPI server on a random local port. Playwright
navigates to `/playground/`. The browser's `fetch("/scenarios/stripe-*")` calls
route to the real backend, which calls the real Stripe test-mode API.
No `page.route()` interception.

## Running locally

```bash
# Install Playwright browsers (once)
uv run python -m playwright install chromium

# Run all Stripe tests
uv run pytest tests/playground/stripe/ --no-cov -v

# Run headed (watch the browser)
PLAYGROUND_HEADED=true uv run pytest tests/playground/stripe/ --no-cov -v -s
```

> **Note:** Stripe tests are excluded from the default `uv run pytest` run and
> from regular CI (push/PR). They must be triggered explicitly.

## Required environment variables

Set these before running (`.env` is loaded automatically if present):

| Variable | Description |
|----------|-------------|
| `STRIPE_API_KEY` | Stripe secret key (`sk_test_...`) |
| `NW_REST_AUTH_DISABLED` | Set to `true` to skip REST auth middleware |

## Optional environment variables (for subscription tests)

| Variable | Description |
|----------|-------------|
| `STRIPE_TEST_CUSTOMER_ID` | Pre-existing Stripe test customer (`cus_...`) |
| `STRIPE_TEST_PRICE_ID` | Pre-existing Stripe test price (`price_...`) |

`test_stripe_cancel_subscription` is automatically skipped when these are absent.

## Test data and cleanup

The `real_stripe_charge_id` session fixture creates a small charge (`$5.00 usd`)
against the `tok_visa` test token once per session. The `test_stripe_refund_by_charge_id`
test immediately refunds this charge in full, so no balance is left outstanding.

The optional `real_stripe_subscription_id` fixture creates a subscription that
is cancelled by `test_stripe_cancel_subscription` — leaving no active subscription
after the session.
