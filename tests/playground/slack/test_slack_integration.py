#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Slack connector Playground real integration tests.

Each test opens the Playground UI, navigates to the Slack panel,
selects an action, fills the form, clicks the run button, and asserts the
resulting pipeline state — no API mocking, real Slack API calls.

Required env vars (loaded from .env):
  SLACK_BOT_TOKEN            — Slack bot token (xoxb-...)

Optional env vars:
  SLACK_TEST_CHANNEL         — target channel (default: #general; bot must be a member)
  SLACK_TEST_USER_ID         — Slack user ID for DM tests (U...); skipped when absent
"""

from __future__ import annotations

import os
import tempfile
import time

from playwright.sync_api import Page, expect

from tests.playground.slack.slack_page import SlackPage
from tests.playground.home_page import PlaygroundHomePage

_TIMEOUT = 25_000  # ms — 4-step pipeline with async Slack API calls


def _navigate_to_slack(page: Page) -> SlackPage:
    PlaygroundHomePage(page).click_connectors()
    slack = SlackPage(page)
    slack.navigate_to_panel()
    return slack


def _maybe_sleep() -> None:
    env = os.getenv("PLAYGROUND_HEADED") or os.getenv("HEADED")
    if env and env.lower().strip() in ("true", "1", "yes"):
        time.sleep(3)


# ── post_message ──────────────────────────────────────────────────────────────


def test_slack_post_message_default(playground_page: Page, slack_test_channel: str) -> None:
    """Post a message with default values; all 4 steps must succeed."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("post_message")
    slack.fill_message_fields(channel=slack_test_channel)
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(slack.summary_text).to_contain_text("post_message")
    expect(slack.summary_text).to_contain_text(slack_test_channel)
    expect(slack.result_tag).to_be_visible()
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(slack.log_terminal).to_contain_text("SUCCESS")

    _maybe_sleep()


def test_slack_post_message_custom_message(
    playground_page: Page, slack_test_channel: str
) -> None:
    """Post a message with custom content; summary must reflect the channel."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("post_message")
    slack.fill_message_fields(
        channel=slack_test_channel,
        message="node-wire integration test — safe to ignore.",
    )
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(slack.summary_text).to_contain_text(slack_test_channel)
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Active")

    _maybe_sleep()


def test_slack_post_message_invalid_channel(playground_page: Page) -> None:
    """Post to a nonexistent channel; step-1 (Dispatch) must show error state."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("post_message")
    slack.fill_message_fields(channel="this-channel-does-not-exist-99999")
    slack.submit()

    # step-0 (Format Slack Payload) is local — always succeeds
    expect(playground_page.locator("#step-0.success")).to_be_visible(timeout=_TIMEOUT)
    # step-1 (Dispatch to Slack API) must fail for an invalid channel
    expect(playground_page.locator("#step-1.error")).to_be_visible(timeout=_TIMEOUT)
    expect(slack.final_result).to_be_hidden()
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Failed")
    expect(slack.log_terminal).to_contain_text("FAILED")

    _maybe_sleep()


# ── send_direct_message ───────────────────────────────────────────────────────


def test_slack_send_direct_message(
    playground_page: Page, slack_test_user_id: str
) -> None:
    """Send a DM to a real user; all 4 steps must succeed."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("send_direct_message")
    slack.fill_message_fields(
        channel=slack_test_user_id,
        message="node-wire DM integration test — safe to ignore.",
    )
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(slack.summary_text).to_contain_text("send_direct_message")
    expect(slack.summary_text).to_contain_text(slack_test_user_id)
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(slack.log_terminal).to_contain_text("SUCCESS")

    _maybe_sleep()


# ── upload_file ───────────────────────────────────────────────────────────────


def test_slack_upload_file(playground_page: Page, slack_test_channel: str) -> None:
    """Attach a temp file and upload it; all 4 steps must succeed."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("upload_file")

    with tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, prefix="nw_slack_test_"
    ) as tmp:
        tmp.write(b"node-wire Slack upload integration test - safe to delete.")
        tmp_path = tmp.name

    slack.file_input.set_input_files(tmp_path)
    expect(slack.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(slack.file_drop_zone).to_be_hidden()
    expect(slack.preview_name).to_contain_text("nw_slack_test_")

    slack.fill_upload_fields(
        channel=slack_test_channel,
        initial_comment="node-wire integration test upload — safe to delete.",
    )
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)

    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(slack.summary_text).to_contain_text("upload_file")
    expect(slack.summary_text).to_contain_text(slack_test_channel)
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Active")
    expect(slack.log_terminal).to_contain_text("SUCCESS")

    _maybe_sleep()


def test_slack_upload_remove_and_reattach(playground_page: Page) -> None:
    """Remove an attached file → drop zone reappears; re-attach → preview is restored."""
    slack = _navigate_to_slack(playground_page)

    slack.select_action("upload_file")

    with tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, prefix="nw_slack_reattach_"
    ) as tmp:
        tmp.write(b"Reattach UI test content - safe to delete.")
        tmp_path = tmp.name

    # Attach
    slack.file_input.set_input_files(tmp_path)
    expect(slack.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(slack.file_drop_zone).to_be_hidden()

    # Remove
    slack.remove_file_btn.click()
    expect(slack.file_chosen_preview).to_be_hidden(timeout=3_000)
    expect(slack.file_drop_zone).to_be_visible()

    # Re-attach
    slack.file_input.set_input_files(tmp_path)
    expect(slack.file_chosen_preview).to_be_visible(timeout=3_000)
    expect(slack.preview_name).to_contain_text("nw_slack_reattach_")


# ── cross-action switch ───────────────────────────────────────────────────────


def test_slack_switch_post_message_then_upload(
    playground_page: Page, slack_test_channel: str
) -> None:
    """Run post_message then switch to upload_file on the same page — both must succeed."""
    slack = _navigate_to_slack(playground_page)

    # First run: post_message
    slack.select_action("post_message")
    slack.fill_message_fields(channel=slack_test_channel)
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)
    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)

    # Switch to upload_file and run
    slack.select_action("upload_file")

    with tempfile.NamedTemporaryFile(
        suffix=".txt", delete=False, prefix="nw_slack_switch_"
    ) as tmp:
        tmp.write(b"Cross-action switch test - safe to delete.")
        tmp_path = tmp.name

    slack.file_input.set_input_files(tmp_path)
    expect(slack.file_chosen_preview).to_be_visible(timeout=3_000)

    slack.fill_upload_fields(channel=slack_test_channel)
    slack.submit()

    for i in range(4):
        expect(playground_page.locator(f"#step-{i}.success")).to_be_visible(timeout=_TIMEOUT)
    expect(slack.final_result).to_be_visible(timeout=_TIMEOUT)
    expect(slack.summary_text).to_contain_text("upload_file")
    expect(playground_page.locator("#slack-run-btn .btn-lbl")).to_have_text("Workflow Active")

    _maybe_sleep()
