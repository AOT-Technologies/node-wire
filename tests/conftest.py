"""Shared pytest configuration.

REST API tests default to ``NW_REST_AUTH_DISABLED=true`` so existing tests do not need
headers. MCP tests default to ``NW_MCP_AUTH_DISABLED=true`` for the same reason.
Tests that assert authentication behavior override these env vars.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _rest_auth_disabled_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
