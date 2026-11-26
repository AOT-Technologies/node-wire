#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import re

from playwright.sync_api import Locator, Page


class EpicFhirPage:
    """Page Object Model for the Epic FHIR card (the generic connector card) in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.card: Locator = page.locator(".connector-card[data-dynamic]").filter(
            has=page.get_by_role("heading", name="Epic FHIR", exact=True)
        )
        self.action_input: Locator = page.get_by_role("combobox", name="Target action")
        self.run_btn: Locator = page.locator(".action-btn", has_text="Run Action")
        self.step: Locator = page.locator("[data-dynamic] .flow-node")
        self.step_status: Locator = self.step.locator(".node-status")
        self.response_btn: Locator = self.step.get_by_role("button", name="Response")
        self.response_body: Locator = self.step.locator(".response-json").first
        self.log_terminal: Locator = page.locator("#log-terminal")

    def open(self) -> None:
        """Open the card from the Connectors view."""
        self.card.click()

    def pick_action(self, action: str) -> None:
        self.action_input.fill(action)
        self.page.get_by_role("option").filter(has_text=re.compile(rf"{action}$")).click()

    def field(self, name: str) -> Locator:
        return self.page.locator(f"input[name='{name}'], textarea[name='{name}']")

    def run(self) -> None:
        self.run_btn.click()
