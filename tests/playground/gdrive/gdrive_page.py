#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from playwright.sync_api import Page, Locator


class GoogleDrivePage:
    """Page Object Model for the Google Drive connector panel in the Playground."""

    def __init__(self, page: Page) -> None:
        self.page = page

        # Selector for the Google Drive card inside system connectors view
        self.connector_card: Locator = page.locator(".connector-card[data-mode='gdrive']")

        # Panel root and main headers
        self.panel: Locator = page.locator("#gdrive-panel")
        self.title: Locator = page.locator("#gdrive-panel .card-title h2")
        self.action_select: Locator = page.locator("#gdrive-action-select")
        self.run_btn: Locator = page.locator("#gdrive-run-btn")
        self.back_to_connectors: Locator = page.locator("#back-to-connectors")

        # --- files.upload action elements ---
        self.upload_section: Locator = page.locator("#gdrive-upload-only")
        self.recipient_email: Locator = page.locator(
            "#gdrive-upload-only input[name='recipient_email']"
        )
        self.doc_name_group: Locator = page.locator("#gdrive-doc-name-group")
        self.document_name: Locator = page.locator(
            "#gdrive-doc-name-group input[name='document_name']"
        )
        self.file_section: Locator = page.locator("#gdrive-file-section")
        self.file_input: Locator = page.locator("#gdrive-file")
        self.file_drop_zone: Locator = page.locator("#file-drop-zone")
        self.file_chosen_preview: Locator = page.locator("#file-chosen-preview")
        self.preview_name: Locator = page.locator("#file-chosen-preview .preview-name")
        self.remove_file_btn: Locator = page.locator("#file-chosen-preview .remove-file-btn")

        # --- files.get action elements ---
        self.get_section: Locator = page.locator("#gdrive-get-only")
        self.get_file_id: Locator = page.locator("#gdrive-get-only input[name='get_file_id']")
        self.get_fields: Locator = page.locator("#gdrive-get-only input[name='get_fields']")

        # --- files.update action elements ---
        self.update_section: Locator = page.locator("#gdrive-update-only")
        self.update_file_id: Locator = page.locator(
            "#gdrive-update-only input[name='update_file_id']"
        )
        self.update_name: Locator = page.locator("#gdrive-update-only input[name='update_name']")
        self.update_mime_type: Locator = page.locator(
            "#gdrive-update-only input[name='update_mime_type']"
        )
        self.update_add_parents: Locator = page.locator(
            "#gdrive-update-only input[name='update_add_parents']"
        )
        self.update_remove_parents: Locator = page.locator(
            "#gdrive-update-only input[name='update_remove_parents']"
        )

        # --- files.list action elements ---
        self.list_section: Locator = page.locator("#gdrive-list-only")
        self.list_page_size: Locator = page.locator(
            "#gdrive-list-only input[name='list_page_size']"
        )
        self.list_query: Locator = page.locator("#gdrive-list-only input[name='list_query']")
        self.list_fields: Locator = page.locator("#gdrive-list-only input[name='list_fields']")

        # --- Output and Logs elements ---
        self.pipeline_steps: Locator = page.locator(".flow-node")
        self.step_nodes: list[Locator] = [page.locator(f"#step-{i}") for i in range(4)]
        self.final_result: Locator = page.locator("#final-result")
        self.summary_text: Locator = page.locator("#human-summary")
        self.result_tag: Locator = page.locator("#result-id")
        self.log_terminal: Locator = page.locator("#log-terminal")

    def navigate_to_panel(self) -> None:
        """Click the Google Drive card in system connectors to open the panel."""
        self.connector_card.click()

    def select_action(self, action: str) -> None:
        """Change the action via the select element."""
        self.action_select.select_option(action)

    def fill_upload_fields(self, recipient_email: str, doc_name: str | None = None) -> None:
        """Fill upload parameters."""
        self.recipient_email.fill(recipient_email)
        if doc_name is not None:
            # First ensure doc_name field is shown by switching to Write Note/sub-mode if needed,
            # or fill directly if exposed.
            self.document_name.fill(doc_name)

    def fill_get_fields(self, file_id: str, fields: str | None = None) -> None:
        """Fill get parameters."""
        self.get_file_id.fill(file_id)
        if fields is not None:
            self.get_fields.fill(fields)

    def fill_update_fields(
        self,
        file_id: str,
        new_name: str | None = None,
        mime_type: str | None = None,
        add_parents: str | None = None,
        remove_parents: str | None = None,
    ) -> None:
        """Fill update parameters."""
        self.update_file_id.fill(file_id)
        if new_name is not None:
            self.update_name.fill(new_name)
        if mime_type is not None:
            self.update_mime_type.fill(mime_type)
        if add_parents is not None:
            self.update_add_parents.fill(add_parents)
        if remove_parents is not None:
            self.update_remove_parents.fill(remove_parents)

    def fill_list_fields(
        self,
        page_size: int | None = None,
        query: str | None = None,
        fields: str | None = None,
    ) -> None:
        """Fill list parameters."""
        if page_size is not None:
            self.list_page_size.fill(str(page_size))
        if query is not None:
            self.list_query.fill(query)
        if fields is not None:
            self.list_fields.fill(fields)

    def submit(self) -> None:
        """Submit the form to execute the archival/orchestration workflow."""
        self.run_btn.click()

    def go_back(self) -> None:
        """Click 'Back to All Connectors' to return to connectors selection view."""
        self.back_to_connectors.click()
