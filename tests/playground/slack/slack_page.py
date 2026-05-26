#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class SlackPage:
    """Page Object Model for the Slack connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Selector for the Slack card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='slack']")

        # Panel root and header
        self.panel: Locator = page.locator("#slack-panel")
        self.title: Locator = page.locator("#slack-panel .card-title h2")
        self.action_select: Locator = page.locator("#slack-action-select")
        self.run_btn: Locator = page.locator("#slack-run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # Shared channel input (always visible)
        self.channel: Locator = page.locator("#slack-panel input[name='channel']")

        # --- post_message / send_direct_message section ---
        self.message_section: Locator = page.locator("#slack-message-section")
        self.message: Locator = page.locator("#slack-message-section textarea[name='message']")

        # --- upload_file section ---
        self.file_section: Locator = page.locator("#slack-file-section")
        self.filename: Locator = page.locator("#slack-file-section input[name='filename']")
        self.initial_comment: Locator = page.locator(
            "#slack-file-section input[name='initial_comment']"
        )
        self.file_input: Locator = page.locator("#slack-file")
        self.file_drop_zone: Locator = page.locator("#slack-file-drop-zone")
        self.file_chosen_preview: Locator = page.locator("#slack-file-chosen-preview")
        self.preview_name: Locator = page.locator("#slack-file-chosen-preview .preview-name")
        self.remove_file_btn: Locator = page.locator("#slack-file-chosen-preview .remove-file-btn")

        # --- Output and Logs elements ---
        self.pipeline_steps: Locator = page.locator(".flow-node")
        self.step_nodes: list[Locator] = [page.locator(f"#step-{i}") for i in range(4)]
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Slack card in system connectors to open the panel."""
        self.connector_card.click()

    def select_action(self, action: str) -> None:
        """Change the action via the select element."""
        self.action_select.select_option(action)

    def fill_message_fields(self, channel: str | None = None, message: str | None = None) -> None:
        """Fill post_message / send_direct_message parameters."""
        if channel is not None:
            self.channel.fill(channel)
        if message is not None:
            self.message.fill(message)

    def fill_upload_fields(
        self,
        channel: str | None = None,
        filename: str | None = None,
        initial_comment: str | None = None,
    ) -> None:
        """Fill upload_file parameters (excluding the file attachment itself)."""
        if channel is not None:
            self.channel.fill(channel)
        if filename is not None:
            self.filename.fill(filename)
        if initial_comment is not None:
            self.initial_comment.fill(initial_comment)

    def submit(self) -> None:
        """Submit the form to execute the Slack workflow."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to connectors selection view."""
        self.back_to_connectors.click()
