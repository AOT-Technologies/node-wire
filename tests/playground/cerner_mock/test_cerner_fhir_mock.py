#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Cerner FHIR card in the Playground, against a mock Cerner with dummy data.

The page and the action list come from the real server. The action call itself is answered by
a Playwright route, so no Cerner credentials and no network are needed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from playwright.sync_api import Page, Route, expect

from tests.playground.cerner_mock.cerner_fhir_page import CernerFhirPage
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
def cerner(playground_page: Page) -> CernerFhirPage:
    PlaygroundHomePage(playground_page).click_connectors()
    page = CernerFhirPage(playground_page)
    page.open()
    return page


def test_card_lists_all_cerner_actions(playground_page: Page) -> None:
    PlaygroundHomePage(playground_page).click_connectors()
    cerner = CernerFhirPage(playground_page)
    expect(cerner.card).to_contain_text("129 actions")


def test_search_form_has_cerner_search_fields(cerner: CernerFhirPage) -> None:
    cerner.pick_action("search_observation")
    for name in ("patient", "category", "code", "date", "count"):
        expect(cerner.field(name)).to_have_count(1)


def test_search_patients_sends_the_form_and_shows_the_result(
    cerner: CernerFhirPage, playground_page: Page
) -> None:
    seen: list[dict[str, Any]] = []
    answer = {
        "success": True,
        "data": {"resources": [DUMMY_PATIENT], "total": 1, "next_url": None, "errors": []},
        "trace_id": "00000000-0000-0000-0000-000000000001",
    }
    playground_page.route(
        "**/connectors/fhir_cerner/search_patients", lambda route: _answer(route, answer, seen)
    )
    cerner.pick_action("search_patients")
    cerner.field("given_name").fill("Dummygiven")
    cerner.field("family_name").fill("Dummyfamily")
    cerner.run()

    expect(cerner.step).to_have_class("flow-node success")
    cerner.response_btn.click()
    expect(cerner.response_body).to_contain_text("dummy-patient-1")
    assert len(seen) == 1
    assert seen[0]["body"]["given_name"] == "Dummygiven"
    assert seen[0]["body"]["family_name"] == "Dummyfamily"


def test_cerner_error_shows_in_the_pipeline(cerner: CernerFhirPage, playground_page: Page) -> None:
    answer = {
        "success": False,
        "data": None,
        "error_code": "CERNER_HTTP_ERROR",
        "error_category": "BUSINESS",
        "message": "HTTP 404: Resource not found",
        "trace_id": "00000000-0000-0000-0000-000000000002",
    }
    playground_page.route(
        "**/connectors/fhir_cerner/read_patient", lambda route: _answer(route, answer, [])
    )
    cerner.pick_action("read_patient")
    cerner.field("resource_id").fill("dummy-missing")
    cerner.run()

    expect(cerner.step).to_have_class("flow-node error")
    expect(cerner.step_status).to_contain_text("CERNER_HTTP_ERROR")
