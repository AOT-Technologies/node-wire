#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for entry-point connector registration and allowlist."""

from __future__ import annotations

from importlib.metadata import EntryPoint
from unittest.mock import patch

import pytest

from node_wire_runtime import connector_registry


def test_auto_register_respects_nw_allowed_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only listed entry point names are imported when NW_ALLOWED_CONNECTORS is set."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "a")

    eps = [
        EntryPoint(name="a", value="node_wire_a.logic", group="node_wire.connectors"),
        EntryPoint(name="b", value="node_wire_b.logic", group="node_wire.connectors"),
    ]

    with (
        patch.object(connector_registry, "entry_points", return_value=eps),
        patch.object(connector_registry.importlib, "import_module") as mock_imp,
    ):
        connector_registry.auto_register()

    imported = [c[0][0] for c in mock_imp.call_args_list]
    assert "node_wire_a.logic" in imported
    assert "node_wire_b.logic" not in imported


def test_auto_register_skips_bad_module_prefix() -> None:
    fake_ep = EntryPoint(
        name="evil",
        value="third_party_evil.logic",
        group="node_wire.connectors",
    )
    assert connector_registry._should_skip_ep(fake_ep, {"evil"}, "node_wire_") is True


def test_allowed_connector_not_skipped_when_prefix_matches() -> None:
    fake_ep = EntryPoint(
        name="http_generic",
        value="node_wire_http_generic.logic",
        group="node_wire.connectors",
    )
    assert connector_registry._should_skip_ep(fake_ep, {"http_generic"}, "node_wire_") is False


def test_logic_module_dotted_path_supports_colon_attr() -> None:
    ep = EntryPoint(
        name="x",
        value="node_wire_x.logic:ConnectorClass",
        group="node_wire.connectors",
    )
    assert connector_registry._logic_module_dotted_path(ep) == "node_wire_x.logic"


def test_auto_register_fallback_import_when_no_entry_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allowed connectors not found via entry points fallback to node_wire_<name>.logic import."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "fallback_test")

    # entry_points() returns empty list
    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module") as mock_imp,
    ):
        loaded = connector_registry.auto_register()

    # Should attempt to import node_wire_fallback_test.logic and .registration
    imported = [c[0][0] for c in mock_imp.call_args_list]
    assert "node_wire_fallback_test.logic" in imported
    assert "node_wire_fallback_test.registration" in imported
    # Both are mocked to succeed, so both should be in loaded
    assert "node_wire_fallback_test.logic" in loaded
    assert "node_wire_fallback_test.registration" in loaded


def test_auto_register_fallback_respects_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback import uses NW_CONNECTOR_MODULE_PREFIX if set."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "fallback_test")
    monkeypatch.setenv("NW_CONNECTOR_MODULE_PREFIX", "custom_")

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module") as mock_imp,
    ):
        connector_registry.auto_register()

    imported = [c[0][0] for c in mock_imp.call_args_list]
    assert "custom_fallback_test.logic" in imported
