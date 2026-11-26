#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import re

from playwright.sync_api import Locator, Page


class GenericCardPage:
    """Page Object Model for a generic connector card (the one built from the connector's actions)."""

    def __init__(self, page: Page, title: str) -> None:
        self.page = page
        self.title = title
        self.cards: Locator = page.locator(".connector-card[data-dynamic]").filter(
            has=page.get_by_role("heading", name=title, exact=True)
        )
        self.action_input: Locator = page.get_by_role("combobox", name="Target action")
        self.run_btn: Locator = page.locator(".action-btn", has_text="Run Action")
        self.step: Locator = page.locator("[data-dynamic] .flow-node")
        self.step_status: Locator = self.step.locator(".node-status")
        self.response_btn: Locator = self.step.get_by_role("button", name="Response")
        self.response_body: Locator = self.step.locator(".response-json").first

    def open(self) -> None:
        """Open the card from the Connectors view."""
        self.cards.first.click()

    def pick_action(self, action: str) -> None:
        self.action_input.fill(action)
        self.page.get_by_role("option").filter(has_text=re.compile(rf"{action}$")).click()

    def show_optional(self) -> None:
        """Open the "Optional fields" section, which is closed when the action has required fields."""
        self.page.locator("summary", has_text="Optional fields").click()

    def field(self, name: str) -> Locator:
        return self.page.locator(f"input[name='{name}'], textarea[name='{name}']")

    def run(self) -> None:
        self.run_btn.click()
