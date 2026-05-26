#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os

import httpx
import pytest

_DEFAULT_CHANNEL = os.environ.get("SLACK_TEST_CHANNEL", "#general")


@pytest.fixture(scope="session", autouse=True)
def slack_connector_available(api_server_url: str) -> None:
    """Skip the entire Slack test session if the connector returns HTTP 500.

    This happens when SLACK_BOT_TOKEN is missing or when NW_ALLOWED_CONNECTORS
    is set but does not include 'slack'. Converts a 25-second timeout per test
    into a single fast skip with a clear reason.
    """
    with httpx.Client(timeout=10) as client:
        resp = client.post(
            f"{api_server_url}/scenarios/slack-messaging",
            json={"action": "post_message", "channel": "#general", "message": "health-check"},
        )
    if resp.status_code == 500:
        detail = resp.json().get("detail", "unknown")
        pytest.skip(
            f"Slack connector not available ({detail}). "
            "Ensure SLACK_BOT_TOKEN is set and 'slack' is in NW_ALLOWED_CONNECTORS (or leave it unset)."
        )


@pytest.fixture(scope="session")
def slack_test_channel() -> str:
    """Slack channel used as the target for post_message and upload_file tests.

    Defaults to #general. Override via SLACK_TEST_CHANNEL env var.
    The bot must be a member of this channel.
    """
    return _DEFAULT_CHANNEL


@pytest.fixture(scope="session")
def slack_test_user_id() -> str:
    """Slack user ID (U...) used as the target for send_direct_message tests.

    Requires SLACK_TEST_USER_ID env var. Tests that depend on this fixture
    are skipped when the var is absent.
    """
    user_id = os.environ.get("SLACK_TEST_USER_ID")
    if not user_id:
        pytest.skip("SLACK_TEST_USER_ID is required for direct message tests")
    return user_id
