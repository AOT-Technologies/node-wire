#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Google Drive connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Google Drive panel,
selects an action, fills the form, clicks the run button, and asserts the
resulting pipeline state — no API mocking, real Google Drive calls.

Required env vars (loaded from .env):
  GOOGLE_DRIVE_SA_JSON       — service-account JSON (path or inline JSON)
  GOOGLE_DRIVE_FOLDER_ID     — target folder for uploads
  GDRIVE_TEST_RECIPIENT_EMAIL — email used as sharing recipient (default: test@mailinator.com)
"""

from __future__ import annotations

import tempfile

from playwright.sync_api import Page, expect

from tests.playground.gdrive.gdrive_page import GoogleDrivePage
from tests.playground.home_page import PlaygroundHomePage
from tests.playground.utils import maybe_sleep

_TIMEOUT_STEP = 20_000  # ms — single-step operations (list, get)
_TIMEOUT_MULTI = 45_000  # ms — multi-step operations (upload, update)


def _navigate_to_gdrive(page: Page) -> GoogleDrivePage:
    PlaygroundHomePage(page).click_connectors()
    gdrive = GoogleDrivePage(page)
    gdrive.navigate_to_panel()
    return gdrive


# ── files.list ────────────────────────────────────────────────────────────────


def test_gdrive_list_files_default_page_size(playground_page: Page) -> None:
    """List files with the default page size; assert the pipeline step succeeds."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.list")
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.summary_text).to_contain_text("file(s)")
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(gdrive.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_gdrive_list_files_explicit_page_size(playground_page: Page) -> None:
    """List files with page_size=5; summary must mention the requested page size."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.list")
    gdrive.fill_list_fields(page_size=5)
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.summary_text).to_contain_text("page size 5")
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(gdrive.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_gdrive_list_files_with_query(playground_page: Page) -> None:
    """List files filtered by mimeType query; step label and success state must appear."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.list")
    gdrive.fill_list_fields(page_size=10, query="mimeType='text/plain'")
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── files.get ─────────────────────────────────────────────────────────────────


def test_gdrive_get_file(playground_page: Page, real_gdrive_file_id: str) -> None:
    """Retrieve metadata for a real file; assert single-step success and result card."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.get")
    gdrive.fill_get_fields(real_gdrive_file_id, "id,name,mimeType")
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.summary_text).to_contain_text("Google Drive file metadata")
    expect(gdrive.result_tag).to_contain_text(real_gdrive_file_id)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(gdrive.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_gdrive_get_file_without_fields(playground_page: Page, real_gdrive_file_id: str) -> None:
    """files.get without a fields mask; Drive returns default metadata fields."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.get")
    gdrive.fill_get_fields(real_gdrive_file_id)  # no fields argument
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


def test_gdrive_get_file_invalid_id(playground_page: Page) -> None:
    """files.get with a nonexistent ID; the pipeline step must show the error state."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.get")
    gdrive.fill_get_fields("this-id-does-not-exist-9999999999")
    gdrive.submit()

    expect(playground_page.locator("#step-0.error")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_hidden()
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(gdrive.log_terminal).to_contain_text("FAILED")

    maybe_sleep()


# ── files.update ──────────────────────────────────────────────────────────────


def test_gdrive_update_file_name(playground_page: Page, uploaded_test_file_id: str) -> None:
    """Rename the integration-test file; assert all 4 update pipeline steps succeed."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.update")
    gdrive.fill_update_fields(
        file_id=uploaded_test_file_id,
        new_name="nw-integration-test-renamed.txt",
    )
    gdrive.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT_MULTI)

    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_MULTI)
    expect(gdrive.summary_text).to_contain_text("Updated")
    expect(gdrive.result_tag).to_contain_text(uploaded_test_file_id)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(gdrive.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_gdrive_update_file_name_and_mime(
    playground_page: Page, uploaded_test_file_id: str
) -> None:
    """Update both the file name and mime_type; all 4 steps must succeed."""
    gdrive = _navigate_to_gdrive(playground_page)

    gdrive.select_action("files.update")
    gdrive.fill_update_fields(
        file_id=uploaded_test_file_id,
        new_name="nw-integration-test-v2.txt",
        mime_type="text/plain",
    )
    gdrive.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT_MULTI)

    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_MULTI)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()


# ── files.upload ──────────────────────────────────────────────────────────────


def test_gdrive_upload_file(playground_page: Page, test_recipient_email: str) -> None:
    """Attach a temp file, fill recipient email, submit, assert all 4 steps succeed."""
    gdrive = _navigate_to_gdrive(playground_page)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, prefix="nw_ui_test_") as tmp:
        tmp.write(b"Integration test document - uploaded via Playwright UI test.")
        tmp_path = tmp.name

    gdrive.file_input.set_input_files(tmp_path)
    expect(gdrive.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(gdrive.file_drop_zone).to_be_hidden()
    expect(gdrive.preview_name).to_contain_text("nw_ui_test_")

    gdrive.fill_upload_fields(test_recipient_email)
    gdrive.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT_MULTI)

    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_MULTI)
    expect(gdrive.summary_text).to_contain_text("archived to Google Drive")
    expect(gdrive.summary_text).to_contain_text(test_recipient_email)
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(gdrive.log_terminal).to_contain_text("SUCCESS")

    maybe_sleep()


def test_gdrive_upload_remove_and_reattach(playground_page: Page) -> None:
    """Remove an attached file → drop zone reappears; re-attach → preview is restored."""
    gdrive = _navigate_to_gdrive(playground_page)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, prefix="nw_reattach_") as tmp:
        tmp.write(b"Reattach UI test content - safe to delete")
        tmp_path = tmp.name

    # Attach
    gdrive.file_input.set_input_files(tmp_path)
    expect(gdrive.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(gdrive.file_drop_zone).to_be_hidden()

    # Remove
    gdrive.remove_file_btn.click()
    expect(gdrive.file_chosen_preview).to_be_hidden(timeout=3_000)
    expect(gdrive.file_drop_zone).to_be_visible()

    # Re-attach
    gdrive.file_input.set_input_files(tmp_path)
    expect(gdrive.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(gdrive.preview_name).to_contain_text("nw_reattach_")


# ── cross-action switch ───────────────────────────────────────────────────────


def test_gdrive_switch_list_then_get(playground_page: Page, real_gdrive_file_id: str) -> None:
    """Run files.list, switch to files.get on the same page — both must complete successfully."""
    gdrive = _navigate_to_gdrive(playground_page)

    # First run: files.list
    gdrive.select_action("files.list")
    gdrive.submit()
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.summary_text).to_contain_text("file(s)")

    # Switch action and run again
    gdrive.select_action("files.get")
    gdrive.fill_get_fields(real_gdrive_file_id)
    gdrive.submit()

    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.final_result).to_be_visible(timeout=_TIMEOUT_STEP)
    expect(gdrive.summary_text).to_contain_text("Google Drive file metadata")
    expect(playground_page.locator("#gdrive-run-btn .btn-lbl")).to_have_text("Workflow Active")

    maybe_sleep()
