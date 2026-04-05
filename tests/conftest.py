"""Shared pytest configuration.

REST API tests default to ``NW_REST_AUTH_DISABLED=true`` so existing tests do not need
headers. Tests that assert authentication behavior override these env vars.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _rest_auth_disabled_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
