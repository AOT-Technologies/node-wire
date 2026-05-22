#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Playground Home page integration test.

This test loads the playground page, asserts all header elements and cards,
and verifies interactive transitions between the home page selection view and
individual dashboard views (Agentic Workflow and Connectors) using the Page Object Model.
"""

from __future__ import annotations

import os
import time
from playwright.sync_api import Page, expect

from tests.playground.home_page import PlaygroundHomePage


def test_playground_home_page_flow(playground_page: Page) -> None:
    """Verify elements visibility, cards presence, and navigation transitions on the Playground Home page."""
    home = PlaygroundHomePage(playground_page)

    # 1. Assert overall page title
    assert playground_page.title() == "node-wire Playground"

    # 2. Verify visibility of root components and headers
    expect(home.root_selection_view).to_be_visible()
    expect(home.main_layout).to_be_hidden()
    expect(home.brand_header).to_be_visible()
    expect(home.brand_title).to_contain_text("node-")
    expect(home.tagline).to_be_visible()
    expect(home.header_actions).to_be_hidden()

    # 3. Assert card counts and detailed card contents
    assert home.selection_cards.count() == 2

    # Agentic Workflow Card
    expect(home.agentic_card).to_be_visible()
    expect(home.agentic_card_title).to_have_text("Agentic Workflow")
    expect(home.agentic_card_desc).to_contain_text("via ToolHive")

    # Connectors Card
    expect(home.connectors_card).to_be_visible()
    expect(home.connectors_card_title).to_have_text("Connectors")
    expect(home.connectors_card_desc).to_contain_text("Pre-built Clinical Workflows")

    # 4. Test Navigation Flow: Root -> Agentic Workflow -> Root
    home.click_agentic_workflow()
    expect(home.root_selection_view).to_be_hidden()
    expect(home.main_layout).to_be_visible()

    # Return back to home
    home.go_back_to_selection()
    expect(home.root_selection_view).to_be_visible()
    expect(home.main_layout).to_be_hidden()

    # 5. Test Navigation Flow: Root -> Connectors -> Root
    home.click_connectors()
    expect(home.root_selection_view).to_be_hidden()
    expect(home.main_layout).to_be_visible()

    # Return back to home
    home.go_back_to_selection()
    expect(home.root_selection_view).to_be_visible()
    expect(home.main_layout).to_be_hidden()

    # 6. Optional visual delay for headed mode
    is_headed = os.getenv("PLAYGROUND_HEADED") or os.getenv("HEADED")
    if is_headed and is_headed.lower().strip() in ("true", "1", "yes"):
        time.sleep(5)
