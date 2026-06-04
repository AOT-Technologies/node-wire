#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class EpicFhirPage:
    """Page Object Model for the Epic FHIR (EHR) connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Selector for the Epic FHIR card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='ehr']")

        # Panel root and header
        self.panel: Locator = page.locator("#ehr-panel")
        self.title: Locator = page.locator("#ehr-panel .card-title h2")
        self.run_btn: Locator = page.locator("#run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # Output and log elements
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Epic FHIR card in system connectors to open the panel."""
        self.connector_card.click()

    def submit(self) -> None:
        """Submit the form to execute the Epic FHIR workflow."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to connectors selection view."""
        self.back_to_connectors.click()
