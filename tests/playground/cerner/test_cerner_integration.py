#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Cerner connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Cerner panel,
clicks the run button with pre-filled defaults, and asserts the resulting
pipeline state — no API mocking, real Cerner FHIR Sandbox calls.

Required env vars (loaded from .env):
  Cerner credentials (e.g. CERNER_CLIENT_ID, CERNER_CLIENT_SECRET, CERNER_BASE_URL)
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playground.cerner.cerner_page import CernerPage
from tests.playground.home_page import PlaygroundHomePage
from tests.playground.utils import maybe_sleep

_TIMEOUT = 25_000  # ms — 4-step pipeline with async Cerner FHIR API calls


def _navigate_to_cerner(page: Page) -> CernerPage:
    PlaygroundHomePage(page).click_connectors()
    cerner = CernerPage(page)
    cerner.navigate_to_panel()
    return cerner


def test_cerner_post_consultation_default(playground_page: Page) -> None:
    """Submit a Cerner consultation with default pre-filled values; all 4 steps must succeed."""
    cerner = _navigate_to_cerner(playground_page)
    cerner.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(cerner.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(cerner.summary_text).to_contain_text("Cerner EHR")
    expect(cerner.result_tag).to_be_visible()
    expect(playground_page.locator("#cerner-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(cerner.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()
