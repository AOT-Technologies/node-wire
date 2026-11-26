#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The Stripe and Slack generic cards in the Playground, against a mock API with dummy data.

The page and the action lists come from the real server. The action call itself is answered by a
Playwright route, so no Stripe or Slack credentials and no network are needed.
"""

from __future__ import annotations

import json
from typing import Any

from playwright.sync_api import Page, Route, expect

from tests.playground.generic_cards.generic_card_page import GenericCardPage
from tests.playground.home_page import PlaygroundHomePage


def _answer(route: Route, body: dict[str, Any], seen: list[dict[str, Any]]) -> None:
    seen.append({"url": route.request.url, "body": route.request.post_data_json})
    route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def _open(page: Page, title: str) -> GenericCardPage:
    PlaygroundHomePage(page).click_connectors()
    card = GenericCardPage(page, title)
    card.open()
    return card


def test_stripe_card_lists_the_full_action_set(playground_page: Page) -> None:
    PlaygroundHomePage(playground_page).click_connectors()
    stripe = GenericCardPage(playground_page, "Stripe")
    expect(stripe.cards).to_have_count(1)
    expect(stripe.cards).to_contain_text("650 actions")


def test_slack_card_is_the_full_connector_and_the_old_name_is_gone(playground_page: Page) -> None:
    PlaygroundHomePage(playground_page).click_connectors()
    expect(GenericCardPage(playground_page, "Slack").cards).to_have_count(1)
    expect(GenericCardPage(playground_page, "Slack").cards).to_contain_text("174 actions")
    expect(GenericCardPage(playground_page, "Slack api").cards).to_have_count(0)


def test_stripe_charge_sends_the_form_and_shows_the_result(playground_page: Page) -> None:
    seen: list[dict[str, Any]] = []
    answer = {
        "success": True,
        "data": {
            "body": {"id": "ch_dummy_1", "object": "charge", "amount": 1500},
            "status_code": 200,
        },
        "trace_id": "00000000-0000-0000-0000-000000000001",
    }
    playground_page.route(
        "**/connectors/stripe/post_charges", lambda route: _answer(route, answer, seen)
    )
    stripe = _open(playground_page, "Stripe")
    stripe.pick_action("post_charges")
    stripe.field("amount").fill("1500")
    stripe.field("currency").fill("usd")
    stripe.run()

    expect(stripe.step).to_have_class("flow-node success")
    stripe.response_btn.click()
    expect(stripe.response_body).to_contain_text("ch_dummy_1")
    assert len(seen) == 1
    assert seen[0]["body"]["amount"] == 1500 and seen[0]["body"]["currency"] == "usd"


def test_slack_post_message_sends_the_form_and_shows_an_error(playground_page: Page) -> None:
    answer = {
        "success": False,
        "data": None,
        "error_code": "HTTP_AUTH_ERROR",
        "error_category": "AUTH",
        "message": "not_authed",
        "trace_id": "00000000-0000-0000-0000-000000000002",
    }
    seen: list[dict[str, Any]] = []
    playground_page.route(
        "**/connectors/slack_api/chat_post_message", lambda route: _answer(route, answer, seen)
    )
    slack = _open(playground_page, "Slack")
    slack.pick_action("chat_post_message")
    slack.field("channel").fill("C0DUMMY")
    slack.show_optional()
    slack.field("text").fill("hello from the demo")
    slack.run()

    expect(slack.step).to_have_class("flow-node error")
    expect(slack.step_status).to_contain_text("HTTP_AUTH_ERROR")
    assert seen[0]["body"]["channel"] == "C0DUMMY"


def test_a_loading_card_shows_until_the_connector_list_arrives(playground_page: Page) -> None:
    held: list[Route] = []  # the answer is held back until the test lets it go
    playground_page.route("**/playground/connectors", lambda route: held.append(route))
    playground_page.reload()
    PlaygroundHomePage(playground_page).click_connectors()

    loading = playground_page.locator(".connector-loading")
    expect(loading).to_be_visible()
    expect(loading).to_contain_text("Loading connectors")
    playground_page.wait_for_timeout(200)
    assert len(held) == 1
    held[0].continue_()

    expect(loading).to_have_count(0, timeout=15_000)
    expect(GenericCardPage(playground_page, "Stripe").cards).to_have_count(1)


def test_a_failed_connector_list_says_so(playground_page: Page) -> None:
    playground_page.route(
        "**/playground/connectors", lambda route: route.fulfill(status=500, body="boom")
    )
    playground_page.reload()
    PlaygroundHomePage(playground_page).click_connectors()

    loading = playground_page.locator(".connector-loading")
    expect(loading).to_contain_text("Could not load connectors")
    expect(loading).to_contain_text("HTTP 500")
