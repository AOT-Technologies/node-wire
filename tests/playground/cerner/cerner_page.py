#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class CernerPage:
    """Page Object Model for the Cerner connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Selector for the Cerner card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='cerner']")

        # Panel root and header
        self.panel: Locator = page.locator("#cerner-panel")
        self.title: Locator = page.locator("#cerner-panel .card-title h2")
        self.run_btn: Locator = page.locator("#cerner-run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # Output and log elements
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Cerner card in system connectors to open the panel."""
        self.connector_card.click()

    def submit(self) -> None:
        """Submit the form to execute the Cerner workflow."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to connectors selection view."""
        self.back_to_connectors.click()
