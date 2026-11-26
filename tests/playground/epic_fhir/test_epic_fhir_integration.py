#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Epic FHIR card in the Playground, against a mock Epic with dummy data.

The page and the action list come from the real server. The action call itself is answered by
a Playwright route, so no Epic credentials and no network are needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page, Route, expect

from tests.playground.epic_fhir.epic_fhir_page import EpicFhirPage
from tests.playground.home_page import PlaygroundHomePage

DUMMY_PATIENT = {
    "resourceType": "Patient",
    "id": "dummy-patient-1",
    "name": [{"family": "Dummyfamily", "given": ["Dummygiven"]}],
    "birthDate": "1990-01-01",
}


def _answer(route: Route, body: dict[str, Any], seen: list[dict[str, Any]]) -> None:
    seen.append({"url": route.request.url, "body": route.request.post_data_json})
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def epic(playground_page: Page) -> EpicFhirPage:
    PlaygroundHomePage(playground_page).click_connectors()
    page = EpicFhirPage(playground_page)
    page.open()
    return page


def test_card_lists_all_epic_actions(playground_page: Page) -> None:
    PlaygroundHomePage(playground_page).click_connectors()
    epic = EpicFhirPage(playground_page)
    expect(epic.card).to_contain_text("137 actions")


def test_search_form_has_epic_search_fields(epic: EpicFhirPage) -> None:
    epic.pick_action("search_observation")
    for name in ("patient", "category", "code", "date", "count"):
        expect(epic.field(name)).to_have_count(1)


def test_search_patients_sends_the_form_and_shows_the_result(
    epic: EpicFhirPage, playground_page: Page
) -> None:
    seen: list[dict[str, Any]] = []
    answer = {
        "success": True,
        "data": {"resources": [DUMMY_PATIENT], "total": 1, "next_url": None, "errors": []},
        "trace_id": "00000000-0000-0000-0000-000000000001",
    }
    playground_page.route(
        "**/connectors/fhir_epic/search_patients", lambda route: _answer(route, answer, seen)
    )
    epic.pick_action("search_patients")
    epic.field("given_name").fill("Dummygiven")
    epic.field("family_name").fill("Dummyfamily")
    epic.run()

    expect(epic.step).to_have_class("flow-node success")
    epic.response_btn.click()
    expect(epic.response_body).to_contain_text("dummy-patient-1")
    assert len(seen) == 1
    assert seen[0]["body"]["given_name"] == "Dummygiven"
    assert seen[0]["body"]["family_name"] == "Dummyfamily"


def test_epic_error_shows_in_the_pipeline(epic: EpicFhirPage, playground_page: Page) -> None:
    answer = {
        "success": False,
        "data": None,
        "error_code": "FHIR_HTTP_ERROR",
        "error_category": "BUSINESS",
        "message": "HTTP 404: Resource not found",
        "trace_id": "00000000-0000-0000-0000-000000000002",
    }
    playground_page.route(
        "**/connectors/fhir_epic/read_patient", lambda route: _answer(route, answer, [])
    )
    epic.pick_action("read_patient")
    epic.field("resource_id").fill("dummy-missing")
    epic.run()

    expect(epic.step).to_have_class("flow-node error")
    expect(epic.step_status).to_contain_text("FHIR_HTTP_ERROR")
