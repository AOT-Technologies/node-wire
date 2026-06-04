#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Salesforce CRM connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Salesforce panel,
selects an action, fills the form, clicks the run button, and asserts the
resulting pipeline state — no API mocking, real Salesforce calls.

Required env vars (loaded from .env):
  SALESFORCE_INSTANCE_URL   — https://<org>.my.salesforce.com
  SALESFORCE_TOKEN_URL      — OAuth2 token endpoint
  SALESFORCE_CLIENT_ID      — Connected App client ID
  SALESFORCE_CLIENT_SECRET  — Connected App client secret
  SALESFORCE_REFRESH_TOKEN  — Long-lived refresh token
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

from tests.playground.home_page import PlaygroundHomePage
from tests.playground.salesforce.helpers import rnd as _rnd, random_email as _email
from tests.playground.salesforce.salesforce_page import SalesforcePage
from tests.playground.utils import maybe_sleep

_TIMEOUT = 20_000  # ms — all Salesforce operations are single-step


def _navigate_to_salesforce(page: Page) -> SalesforcePage:
    PlaygroundHomePage(page).click_connectors()
    sf = SalesforcePage(page)
    sf.navigate_to_panel()
    return sf


# ── create_lead ───────────────────────────────────────────────────────────────


def test_sf_create_lead_minimal(playground_page: Page) -> None:
    """Create a Lead with only the required fields (LastName + Company)."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("create_lead")
    sf.fill_lead_fields(last_name=f"Lead{_rnd()}", company=f"Corp{_rnd()}")
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("Lead created successfully")
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_create_lead_full(playground_page: Page) -> None:
    """Create a Lead with first name and email in addition to required fields."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("create_lead")
    sf.fill_lead_fields(
        last_name=f"Lead{_rnd()}",
        company=f"Corp{_rnd()}",
        first_name="John",
        email=_email(),
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("Lead created successfully")
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── create_contact ────────────────────────────────────────────────────────────


def test_sf_create_contact_minimal(playground_page: Page) -> None:
    """Create a Contact with only the required LastName field."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("create_contact")
    sf.fill_contact_fields(last_name=f"Contact{_rnd()}")
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("Contact created successfully")
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_create_contact_with_email(playground_page: Page) -> None:
    """Create a Contact with first name and email."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("create_contact")
    sf.fill_contact_fields(
        last_name=f"Contact{_rnd()}",
        first_name="Jane",
        email=_email(),
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("Contact created successfully")
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── read_lead ─────────────────────────────────────────────────────────────────


def test_sf_read_lead(playground_page: Page, real_sf_lead_id: str) -> None:
    """Retrieve metadata for a real Lead; assert single-step success and result card."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("read_lead")
    sf.fill_id_only(real_sf_lead_id)
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text(real_sf_lead_id)
    expect(sf.result_tag).to_contain_text(real_sf_lead_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_read_lead_invalid_id(playground_page: Page) -> None:
    """read_lead with a nonexistent ID; pipeline step must show the error state."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("read_lead")
    sf.fill_id_only("00Q000000000001AAA")
    sf.submit()

    expect(playground_page.locator("#step-0.error")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_hidden()
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(sf.log_terminal).to_contain_text("FAILED")

    maybe_sleep()


# ── read_contact ──────────────────────────────────────────────────────────────


def test_sf_read_contact(playground_page: Page, real_sf_contact_id: str) -> None:
    """Retrieve metadata for a real Contact; assert single-step success and result card."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("read_contact")
    sf.fill_id_only(real_sf_contact_id)
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text(real_sf_contact_id)
    expect(sf.result_tag).to_contain_text(real_sf_contact_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_read_contact_invalid_id(playground_page: Page) -> None:
    """read_contact with a nonexistent ID; pipeline step must show the error state."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("read_contact")
    sf.fill_id_only("003000000000001AAA")
    sf.submit()

    expect(playground_page.locator("#step-0.error")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_hidden()
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(sf.log_terminal).to_contain_text("FAILED")

    maybe_sleep()


# ── update_lead ───────────────────────────────────────────────────────────────


def test_sf_update_lead(playground_page: Page, real_sf_lead_id: str) -> None:
    """Update a Lead's last name; assert single-step success and summary contains the record ID."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("update_lead")
    sf.fill_lead_update_fields(
        record_id=real_sf_lead_id,
        last_name=f"Lead{_rnd()}",
        company=f"Corp{_rnd()}",
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("updated successfully")
    expect(sf.result_tag).to_contain_text(real_sf_lead_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_update_lead_email(playground_page: Page, real_sf_lead_id: str) -> None:
    """Update only a Lead's email; assert success with result ID."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("update_lead")
    sf.fill_lead_update_fields(
        record_id=real_sf_lead_id,
        email=_email(),
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.result_tag).to_contain_text(real_sf_lead_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── update_contact ────────────────────────────────────────────────────────────


def test_sf_update_contact(playground_page: Page, real_sf_contact_id: str) -> None:
    """Update a Contact's name; assert single-step success and summary contains the record ID."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("update_contact")
    sf.fill_contact_update_fields(
        record_id=real_sf_contact_id,
        last_name=f"Contact{_rnd()}",
        first_name="Updated",
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("updated successfully")
    expect(sf.result_tag).to_contain_text(real_sf_contact_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_sf_update_contact_email(playground_page: Page, real_sf_contact_id: str) -> None:
    """Update only a Contact's email; assert success."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("update_contact")
    sf.fill_contact_update_fields(
        record_id=real_sf_contact_id,
        email=_email(),
    )
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.result_tag).to_contain_text(real_sf_contact_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── delete_lead ───────────────────────────────────────────────────────────────


def test_sf_delete_lead(playground_page: Page, deletable_lead_id: str) -> None:
    """Delete a Lead; assert single-step success and the record ID appears in the result."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("delete_lead")
    sf.fill_id_only(deletable_lead_id)
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text(deletable_lead_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


# ── delete_contact ────────────────────────────────────────────────────────────


def test_sf_delete_contact(playground_page: Page, deletable_contact_id: str) -> None:
    """Delete a Contact; assert single-step success and the record ID appears in the result."""
    sf = _navigate_to_salesforce(playground_page)

    sf.select_action("delete_contact")
    sf.fill_id_only(deletable_contact_id)
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text(deletable_contact_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(sf.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


# ── cross-action switch ───────────────────────────────────────────────────────


def test_sf_switch_create_lead_to_read(playground_page: Page, real_sf_lead_id: str) -> None:
    """Create a Lead, then switch to read_lead on the same page — both must succeed."""
    sf = _navigate_to_salesforce(playground_page)

    # First run: create_lead
    sf.select_action("create_lead")
    sf.fill_lead_fields(last_name=f"Lead{_rnd()}", company=f"Corp{_rnd()}")
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text("Lead created successfully")

    # Switch action and run read_lead
    sf.select_action("read_lead")
    sf.fill_id_only(real_sf_lead_id)
    sf.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    expect(sf.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(sf.summary_text).to_contain_text(real_sf_lead_id)
    expect(playground_page.locator("#salesforce-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()
