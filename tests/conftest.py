#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Shared pytest configuration.

REST API tests default to ``NW_REST_AUTH_DISABLED=true`` so existing tests do not need
headers. MCP tests default to ``NW_MCP_AUTH_DISABLED=true`` for the same reason.
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
os.environ["NW_ALLOWED_CONNECTORS"] = (
    "http_generic,smtp,stripe,google_drive,fhir_epic,fhir_cerner,salesforce,slack"
)
# Skip REST bind dotenv so repo `.env` cannot override the allowlist above during collection/import.
os.environ["NW_REST_LOAD_DOTENV"] = "false"
# Keep the key present (empty) so later load_dotenv(override=False) cannot inject a local
# `.env` value like google_user_oauth before collection-time factory.load().
os.environ["GOOGLE_DRIVE_AUTH_PROVIDER"] = ""
# Test fixture enables all eight publishable connectors (see tests/fixtures/connectors_for_tests.yaml).
os.environ["NW_CONFIG_PATH"] = str(_TESTS_ROOT / "fixtures" / "connectors_for_tests.yaml")
os.environ["NW_JWT_AUDIENCE"] = "node-wire-test"
os.environ["NW_JWT_ISSUER"] = "node-wire-test-issuer"


def _assert_no_cython_so_shadowing_src() -> None:
    """Fail fast if in-tree Cython ``.so``/``.pyd`` files would steal imports from ``.py``.

    Local ``pip install -e packages/runtime`` / wheel builds drop extension modules next to
    sources under ``src/``. pytest-cov then attributes 0% to the shadowed ``.py`` files and
    the 80% gate collapses even when tests pass.
    """
    src_root = _TESTS_ROOT.parent / "src"
    if not src_root.is_dir():
        return
    shadowed = sorted(src_root.rglob("*.so")) + sorted(src_root.rglob("*.pyd"))
    if not shadowed:
        return
    sample = ", ".join(str(p.relative_to(src_root.parent)) for p in shadowed[:5])
    more = f" (+{len(shadowed) - 5} more)" if len(shadowed) > 5 else ""
    raise RuntimeError(
        "Cython extension modules under src/ shadow .py sources for coverage. "
        f"Remove them before pytest (e.g. `find src -name '*.so' -delete`). "
        f"Found {len(shadowed)}: {sample}{more}"
    )


_assert_no_cython_so_shadowing_src()


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
        "node_wire_salesforce.logic",
        "node_wire_slack.logic",
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
    monkeypatch.setenv("NW_MCP_AUTH_DISABLED", "true")
    # Isolate from developer .env: legacy REST tests expect single-tenant unless
    # a test explicitly enables multitenancy.
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    # Keep present+empty so load_dotenv(override=False) cannot re-inject local `.env`.
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PROVIDER", "")
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv("NW_JWT_AUDIENCE", "node-wire-test")
    monkeypatch.setenv("NW_JWT_ISSUER", "node-wire-test-issuer")
    monkeypatch.setenv("NW_RATE_LIMIT_BURST", "1000")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_REFILL_RATE", "100.0")  # Increase for tests
    monkeypatch.setenv("NW_RATE_LIMIT_DISABLED", "true")  # Disable rate limiting for tests


@pytest.fixture(autouse=True)
def _tenants_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tenants.yaml off the repo and clear overlay between tests."""
    path = tmp_path / "tenants.yaml"
    monkeypatch.setenv("NW_TENANTS_PATH", str(path))
    from node_wire_runtime.secrets import OverlaySecretProvider
    import node_wire_runtime.tenant_persistence as pt

    OverlaySecretProvider.instance().clear()
    pt._nested_secrets_mirror.clear()
    yield
    OverlaySecretProvider.instance().clear()
    pt._nested_secrets_mirror.clear()
