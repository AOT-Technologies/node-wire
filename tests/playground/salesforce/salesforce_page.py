#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class SalesforcePage:
    """Page Object Model for the Salesforce CRM connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Connector card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='salesforce']")

        # Panel root and top-level controls
        self.panel: Locator = page.locator("#salesforce-panel")
        self.action_select: Locator = page.locator("#salesforce-action-select")
        self.run_btn: Locator = page.locator("#salesforce-run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # --- Lead section (create_lead / update_lead) ---
        self.lead_section: Locator = page.locator("#salesforce-section-lead")
        self.lead_id: Locator = page.locator("#salesforce-section-lead input[name='lead_id']")
        self.lead_first_name: Locator = page.locator(
            "#salesforce-section-lead input[name='lead_first_name']"
        )
        self.lead_last_name: Locator = page.locator(
            "#salesforce-section-lead input[name='lead_last_name']"
        )
        self.lead_company: Locator = page.locator(
            "#salesforce-section-lead input[name='lead_company']"
        )
        self.lead_email: Locator = page.locator("#salesforce-section-lead input[name='lead_email']")

        # --- Contact section (create_contact / update_contact) ---
        self.contact_section: Locator = page.locator("#salesforce-section-contact")
        self.contact_id: Locator = page.locator(
            "#salesforce-section-contact input[name='contact_id']"
        )
        self.contact_first_name: Locator = page.locator(
            "#salesforce-section-contact input[name='contact_first_name']"
        )
        self.contact_last_name: Locator = page.locator(
            "#salesforce-section-contact input[name='contact_last_name']"
        )
        self.contact_email: Locator = page.locator(
            "#salesforce-section-contact input[name='contact_email']"
        )
        self.contact_account_id: Locator = page.locator(
            "#salesforce-section-contact input[name='contact_account_id']"
        )

        # --- Generic ID section (read_lead / read_contact / delete_lead / delete_contact) ---
        self.id_only_section: Locator = page.locator("#salesforce-section-id-only")
        self.generic_record_id: Locator = page.locator(
            "#salesforce-section-id-only input[name='generic_record_id']"
        )

        # --- Output / log elements (shared across connectors) ---
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Salesforce card in system connectors to open the panel."""
        self.connector_card.click()

    def select_action(self, action: str) -> None:
        """Change the CRM action via the select element."""
        self.action_select.select_option(action)

    def fill_lead_fields(
        self,
        last_name: str,
        company: str,
        first_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Fill Lead create form fields."""
        self.lead_last_name.fill(last_name)
        self.lead_company.fill(company)
        if first_name is not None:
            self.lead_first_name.fill(first_name)
        if email is not None:
            self.lead_email.fill(email)

    def fill_lead_update_fields(
        self,
        record_id: str,
        last_name: str | None = None,
        company: str | None = None,
        first_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Fill Lead update form fields (record ID + any changed fields)."""
        self.lead_id.fill(record_id)
        if last_name is not None:
            self.lead_last_name.fill(last_name)
        if company is not None:
            self.lead_company.fill(company)
        if first_name is not None:
            self.lead_first_name.fill(first_name)
        if email is not None:
            self.lead_email.fill(email)

    def fill_contact_fields(
        self,
        last_name: str,
        first_name: str | None = None,
        email: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """Fill Contact create form fields."""
        self.contact_last_name.fill(last_name)
        if first_name is not None:
            self.contact_first_name.fill(first_name)
        if email is not None:
            self.contact_email.fill(email)
        if account_id is not None:
            self.contact_account_id.fill(account_id)

    def fill_contact_update_fields(
        self,
        record_id: str,
        last_name: str | None = None,
        first_name: str | None = None,
        email: str | None = None,
    ) -> None:
        """Fill Contact update form fields (record ID + any changed fields)."""
        self.contact_id.fill(record_id)
        if last_name is not None:
            self.contact_last_name.fill(last_name)
        if first_name is not None:
            self.contact_first_name.fill(first_name)
        if email is not None:
            self.contact_email.fill(email)

    def fill_id_only(self, record_id: str) -> None:
        """Fill the generic record ID field used by read/delete actions."""
        self.generic_record_id.fill(record_id)

    def submit(self) -> None:
        """Click the run button to execute the selected CRM action."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to the connectors selection view."""
        self.back_to_connectors.click()
