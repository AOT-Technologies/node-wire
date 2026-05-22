#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class PlaygroundHomePage:
    """Page Object Model for the node-wire Playground Home (landing/selection) page."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Section views
        self.root_selection_view: Locator = page.locator("#root-selection-view")
        self.main_layout: Locator = page.locator(".layout-main")

        # Header components
        self.brand_header: Locator = page.locator(".dashboard-header")
        self.brand_title: Locator = page.locator(".brand-text h1")
        self.tagline: Locator = page.locator(".tagline")
        self.header_actions: Locator = page.locator("#header-actions")

        # Selection Cards
        self.selection_cards: Locator = page.locator(".selection-card")

        # Agentic Workflow Card
        self.agentic_card: Locator = page.locator(".selection-card.card-mcp")
        self.agentic_card_title: Locator = self.agentic_card.locator("h3")
        self.agentic_card_desc: Locator = self.agentic_card.locator("p")

        # Connectors Card
        self.connectors_card: Locator = page.locator(".selection-card.card-connectors")
        self.connectors_card_title: Locator = self.connectors_card.locator("h3")
        self.connectors_card_desc: Locator = self.connectors_card.locator("p")

        # Navigation
        self.back_selection_btn: Locator = page.locator("#back-selection-btn")

    def click_agentic_workflow(self) -> None:
        """Click the Agentic Workflow (MCP) selection card to navigate to the agent view."""
        self.agentic_card.click()

    def click_connectors(self) -> None:
        """Click the Connectors selection card to navigate to the clinical workflows view."""
        self.connectors_card.click()

    def go_back_to_selection(self) -> None:
        """Click the back button to return to the selection page."""
        self.back_selection_btn.click()
