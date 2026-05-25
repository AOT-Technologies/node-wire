#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class StripePage:
    """Page Object Model for the Stripe connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Selector for the Stripe card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='stripe']")

        # Panel root and main headers
        self.panel: Locator = page.locator("#stripe-panel")
        self.title: Locator = page.locator("#stripe-panel .card-title h2")
        self.action_select: Locator = page.locator("#stripe-action-select")
        self.run_btn: Locator = page.locator("#stripe-run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # --- charge action elements ---
        self.charge_section: Locator = page.locator("#stripe-section-charge")
        self.charge_amount: Locator = page.locator(
            "#stripe-section-charge input[name='charge_amount']"
        )
        self.charge_currency: Locator = page.locator(
            "#stripe-section-charge input[name='charge_currency']"
        )
        self.charge_description: Locator = page.locator(
            "#stripe-section-charge input[name='charge_description']"
        )

        # --- payment_intent action elements ---
        self.pi_section: Locator = page.locator("#stripe-section-pi")
        self.pi_amount: Locator = page.locator("#stripe-section-pi input[name='pi_amount']")
        self.pi_currency: Locator = page.locator("#stripe-section-pi input[name='pi_currency']")
        self.pi_customer: Locator = page.locator("#stripe-section-pi input[name='pi_customer']")
        self.pi_payment_method: Locator = page.locator(
            "#stripe-section-pi input[name='pi_payment_method']"
        )

        # --- subscription action elements ---
        self.sub_section: Locator = page.locator("#stripe-section-sub")
        self.sub_customer: Locator = page.locator("#stripe-section-sub input[name='sub_customer']")
        self.sub_price: Locator = page.locator("#stripe-section-sub input[name='sub_price']")

        # --- cancel_subscription action elements ---
        self.cancel_section: Locator = page.locator("#stripe-section-cancel")
        self.cancel_sub_id: Locator = page.locator(
            "#stripe-section-cancel input[name='cancel_sub_id']"
        )

        # --- refund action elements ---
        self.refund_section: Locator = page.locator("#stripe-section-refund")
        self.refund_target_id: Locator = page.locator(
            "#stripe-section-refund input[name='refund_target_id']"
        )
        self.refund_amount: Locator = page.locator(
            "#stripe-section-refund input[name='refund_amount']"
        )

        # --- Output and Logs elements ---
        self.pipeline_steps: Locator = page.locator(".flow-node")
        self.step_nodes: list[Locator] = [page.locator(f"#step-{i}") for i in range(3)]
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Stripe card in system connectors to open the panel."""
        self.connector_card.click()

    def select_action(self, action: str) -> None:
        """Change the action via the select element."""
        self.action_select.select_option(action)

    def fill_charge_fields(
        self,
        amount: int | None = None,
        currency: str | None = None,
        description: str | None = None,
    ) -> None:
        """Fill charge parameters (all optional — HTML defaults apply if not provided)."""
        if amount is not None:
            self.charge_amount.fill(str(amount))
        if currency is not None:
            self.charge_currency.fill(currency)
        if description is not None:
            self.charge_description.fill(description)

    def fill_payment_intent_fields(
        self,
        amount: int | None = None,
        currency: str | None = None,
        customer_id: str | None = None,
        payment_method: str | None = None,
    ) -> None:
        """Fill payment intent parameters."""
        if amount is not None:
            self.pi_amount.fill(str(amount))
        if currency is not None:
            self.pi_currency.fill(currency)
        if customer_id is not None:
            self.pi_customer.fill(customer_id)
        if payment_method is not None:
            self.pi_payment_method.fill(payment_method)

    def fill_subscription_fields(self, customer_id: str, price_id: str) -> None:
        """Fill subscription parameters."""
        self.sub_customer.fill(customer_id)
        self.sub_price.fill(price_id)

    def fill_cancel_fields(self, subscription_id: str) -> None:
        """Fill cancel subscription parameter."""
        self.cancel_sub_id.fill(subscription_id)

    def fill_refund_fields(self, target_id: str, amount: int | None = None) -> None:
        """Fill refund parameters. target_id may be a ch_... or pi_... ID."""
        self.refund_target_id.fill(target_id)
        if amount is not None:
            self.refund_amount.fill(str(amount))

    def submit(self) -> None:
        """Submit the form to execute the Stripe workflow."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to connectors selection view."""
        self.back_to_connectors.click()
