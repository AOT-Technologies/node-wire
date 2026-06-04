#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Stripe connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Stripe panel,
selects an action, fills the form, clicks the run button, and asserts the
resulting pipeline state — no API mocking, real Stripe test-mode calls.

Required env vars (loaded from .env):
  STRIPE_API_KEY             — Stripe secret key (sk_test_...)

Optional env vars (for subscription-related tests):
  STRIPE_TEST_CUSTOMER_ID    — pre-existing Stripe test customer (cus_...)
  STRIPE_TEST_PRICE_ID       — pre-existing Stripe test price (price_...)
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playground.stripe.stripe_page import StripePage
from tests.playground.home_page import PlaygroundHomePage
from tests.playground.utils import maybe_sleep

_TIMEOUT = 20_000  # ms — all Stripe scenarios are 3-step


def _navigate_to_stripe(page: Page) -> StripePage:
    PlaygroundHomePage(page).click_connectors()
    stripe = StripePage(page)
    stripe.navigate_to_panel()
    return stripe


# ── charge ────────────────────────────────────────────────────────────────────


def test_stripe_charge_default(playground_page: Page) -> None:
    """Process a charge with the HTML default values (2000 usd); all 3 steps must succeed."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("charge")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("20.00 USD charge")
    expect(stripe.result_tag).to_be_visible()
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(stripe.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_stripe_charge_custom_amount(playground_page: Page) -> None:
    """Process a charge with a custom amount and description; summary must reflect the amount."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("charge")
    stripe.fill_charge_fields(amount=1500, currency="usd", description="nw-test charge")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("15.00 USD charge")
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(stripe.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_stripe_charge_no_description(playground_page: Page) -> None:
    """Process a charge with an empty description; pipeline must still complete successfully."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("charge")
    stripe.fill_charge_fields(amount=1000, currency="usd", description="")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── payment_intent ────────────────────────────────────────────────────────────


def test_stripe_payment_intent_default(playground_page: Page) -> None:
    """Create a payment intent with the HTML defaults (5000 usd, pm_card_visa); 3 steps succeed."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("payment_intent")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("payment intent")
    expect(stripe.result_tag).to_be_visible()
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(stripe.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_stripe_payment_intent_custom_amount(playground_page: Page) -> None:
    """Create a payment intent with a custom amount; result tag must contain a pi_ ID."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("payment_intent")
    stripe.fill_payment_intent_fields(amount=3000, currency="usd")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.result_tag).to_contain_text("pi_")
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


def test_stripe_payment_intent_no_payment_method(playground_page: Page) -> None:
    """Create a payment intent without a payment method; backend creates a requires_payment_method PI."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("payment_intent")
    stripe.fill_payment_intent_fields(amount=2500, currency="usd", payment_method="")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── cancel_subscription ───────────────────────────────────────────────────────


def test_stripe_cancel_subscription_invalid_id(playground_page: Page) -> None:
    """Cancel with a nonexistent subscription ID; step-1 must show error state."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("cancel_subscription")
    stripe.fill_cancel_fields("sub_this_does_not_exist_9999")
    stripe.submit()

    # step-0 (Locate Resource) is a validation step — it always succeeds
    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    # step-1 (Cancel Sub) calls the real Stripe API — it must fail
    expect(playground_page.locator("#step-1.error")).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.final_result).to_be_hidden()
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(stripe.log_terminal).to_contain_text("FAILED")

    maybe_sleep()


def test_stripe_cancel_subscription(
    playground_page: Page, real_stripe_subscription_id: str
) -> None:
    """Cancel a real subscription; all 3 steps must succeed."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("cancel_subscription")
    stripe.fill_cancel_fields(real_stripe_subscription_id)
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("canceled subscription")
    expect(stripe.result_tag).to_contain_text(real_stripe_subscription_id)
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(stripe.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


# ── refund ────────────────────────────────────────────────────────────────────


def test_stripe_refund_by_charge_id(playground_page: Page, real_stripe_charge_id: str) -> None:
    """Issue a full refund against a real charge; all 3 steps must succeed."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("refund")
    stripe.fill_refund_fields(real_stripe_charge_id)
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("issued refund")
    expect(stripe.result_tag).to_be_visible()
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(stripe.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_stripe_refund_invalid_id(playground_page: Page) -> None:
    """Refund with a nonexistent charge ID; step-1 must show error state."""
    stripe = _navigate_to_stripe(playground_page)

    stripe.select_action("refund")
    stripe.fill_refund_fields("ch_this_does_not_exist_9999")
    stripe.submit()

    # step-0 (Validate Params) is a local validation step — it always succeeds
    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    # step-1 (Issue Refund) calls the real Stripe API — it must fail
    expect(playground_page.locator("#step-1.error")).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.final_result).to_be_hidden()
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(stripe.log_terminal).to_contain_text("FAILED")

    maybe_sleep()


# ── cross-action switch ───────────────────────────────────────────────────────


def test_stripe_switch_charge_then_payment_intent(playground_page: Page) -> None:
    """Run a charge, then switch to payment_intent on the same page — both must succeed."""
    stripe = _navigate_to_stripe(playground_page)

    # First run: charge
    stripe.select_action("charge")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)

    # Switch action and run again
    stripe.select_action("payment_intent")
    stripe.submit()

    for i in range(3):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(stripe.summary_text).to_contain_text("payment intent")
    expect(playground_page.locator("#stripe-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()
