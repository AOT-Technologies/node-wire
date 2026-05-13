#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Shared pytest configuration.

REST API tests default to ``NW_REST_AUTH_DISABLED=true`` so existing tests do not need
headers. MCP tests default to ``NW_MCP_AUTH_ENABLED=true`` for the same reason.
Tests that assert authentication behavior override these env vars.
"""

from __future__ import annotations

import os
import pytest

# Ensure tests can import app.py which builds dynamic routes via factory (needs allowed connectors to not crash M3 fail-fast)
os.environ["NW_ALLOWED_CONNECTORS"] = "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner,salesforce,slack"


@pytest.fixture(autouse=True)
def _rest_auth_disabled_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_RATE_LIMIT_BURST", "1000")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_REFILL_RATE", "100.0")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")  # Disable rate limiting for tests
