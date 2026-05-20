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
import importlib
import warnings
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).resolve().parent

# Ensure tests can import app.py which builds dynamic routes via factory (needs allowed connectors to not crash M3 fail-fast)
os.environ["NW_ALLOWED_CONNECTORS"] = "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner"
# Skip REST bind dotenv so repo `.env` cannot override the allowlist above during collection/import.
os.environ["NW_REST_LOAD_DOTENV"] = "false"
# Use a connector config where optional connectors (e.g. slack, salesforce) are disabled so CI and
# devs without those packages still match the narrow allowlist (see tests/fixtures/connectors_for_tests.yaml).
os.environ["NW_CONFIG_PATH"] = str(_TESTS_ROOT / "fixtures" / "connectors_for_tests.yaml")


def _preload_connector_logic_modules() -> None:
    """Register connectors without relying on ``importlib.metadata`` entry points.

    Ensures :func:`bindings.rest_api.app._build_dynamic_routes` sees connectors when
    tests run with ``PYTHONPATH=src`` but without an editable install.
    """
    for mod in (
        "node_wire_http_generic.logic",
        "node_wire_smtp.logic",
        "node_wire_stripe.logic",
        "node_wire_google_drive.logic",
        "node_wire_fhir_epic.logic",
        "node_wire_fhir_cerner.logic",
    ):
        try:
            importlib.import_module(mod)
        except ImportError as exc:
            warnings.warn(
                f"tests: could not import {mod!r} (connectors may be missing in this env): {exc}",
                UserWarning,
                stacklevel=2,
            )
        except Exception as exc:
            raise RuntimeError(
                f"tests: unexpected error importing connector module {mod!r}"
            ) from exc


_preload_connector_logic_modules()


@pytest.fixture(autouse=True)
def _rest_auth_disabled_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_AUTH_DISABLED", "true")
    monkeypatch.setenv("NW_MCP_AUTH_ENABLED", "true")
    monkeypatch.setenv("NW_RATE_LIMIT_BURST", "1000")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_REFILL_RATE", "100.0")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")  # Disable rate limiting for tests
