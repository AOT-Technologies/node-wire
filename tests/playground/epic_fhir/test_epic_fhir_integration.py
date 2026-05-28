#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Epic FHIR connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Epic FHIR panel,
clicks the run button with pre-filled defaults, and asserts the resulting
pipeline state — no API mocking, real Epic FHIR Sandbox calls.

Required env vars (loaded from .env):
  Epic FHIR credentials (e.g. EPIC_CLIENT_ID, EPIC_CLIENT_SECRET, EPIC_BASE_URL)
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playground.epic_fhir.epic_fhir_page import EpicFhirPage
from tests.playground.home_page import PlaygroundHomePage
from tests.playground.utils import maybe_sleep

_TIMEOUT = 25_000  # ms — 4-step pipeline with async Epic FHIR API calls


def _navigate_to_epic_fhir(page: Page) -> EpicFhirPage:
    PlaygroundHomePage(page).click_connectors()
    epic = EpicFhirPage(page)
    epic.navigate_to_panel()
    return epic


def test_epic_fhir_post_consultation_default(playground_page: Page) -> None:
    """Submit a consultation with default pre-filled values; all 4 steps must succeed."""
    epic = _navigate_to_epic_fhir(playground_page)
    epic.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(epic.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(epic.summary_text).to_contain_text("Epic")
    expect(epic.result_tag).to_be_visible()
    expect(playground_page.locator("#run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(epic.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()
