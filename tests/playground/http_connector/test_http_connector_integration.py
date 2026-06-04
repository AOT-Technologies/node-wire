#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""HTTP connector Playground real integration tests.

Each test opens the Playground UI, navigates to the HTTP connector (IT Ops)
panel, clicks the run button with pre-filled defaults, and asserts the
resulting pipeline state — no API mocking, real HTTP calls to httpbin.org.

No credentials required; http_generic uses a public endpoint.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playground.http_connector.http_connector_page import HttpConnectorPage
from tests.playground.home_page import PlaygroundHomePage
from tests.playground.utils import maybe_sleep

_TIMEOUT = 20_000  # ms — 4-step pipeline with httpbin.org calls


def _navigate_to_http_connector(page: Page) -> HttpConnectorPage:
    PlaygroundHomePage(page).click_connectors()
    http = HttpConnectorPage(page)
    http.navigate_to_panel()
    return http


def test_http_connector_submit_incident_default(playground_page: Page) -> None:
    """Submit an IT incident with default pre-filled values; all 4 steps must succeed."""
    http = _navigate_to_http_connector(playground_page)
    http.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(http.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(http.summary_text).to_contain_text("IT Incident")
    expect(http.result_tag).to_be_visible()
    expect(playground_page.locator("#itops-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(http.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()
