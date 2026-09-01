#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""node_wire_runtime.tenant_session.TenantSessionOverlay — in isolation.

Collaborators (store lookups, config coverage) are fakes; nothing here
touches bindings.mcp_server or bindings.factory. See ticket 1 ("Build
TenantSessionOverlay") of .scratch/tenant-session-overlay/map.md.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest

from node_wire_runtime.config_store import DEFAULT_TENANT
from node_wire_runtime.tenant_session import (
    MISSING_TENANT_SELECT_MESSAGE,
    PIN_LOCKED_MESSAGE,
    TenantSessionOverlay,
    allowed_tenants_from_env,
    pin_locked_from_env,
)


def _overlay(
    *,
    tenants: Optional[Dict[str, Dict[str, List[str]]]] = None,
    pin_locked: bool = False,
    allowed: Optional[frozenset[str]] = None,
) -> TenantSessionOverlay:
    """Build an overlay over an in-memory fake store: tenants maps
    tenant_id -> {config_name: [connector_ids with that config]}."""
    tenants = tenants if tenants is not None else {}

    def store_has_tenant(tenant_id: str) -> bool:
        return tenant_id in tenants

    def config_coverage(tenant_id: str, config_name: str) -> Tuple[List[str], List[str]]:
        configs = tenants.get(tenant_id, {})
        have = configs.get(config_name, [])
        missing = [cid for cid in {"c1", "c2"} if cid not in have]
        return have, missing

    return TenantSessionOverlay(
        store_has_tenant=store_has_tenant,
        config_coverage=config_coverage,
        pin_locked=lambda: pin_locked,
        allowed_tenants=lambda: allowed,
    )


# --------------------------------------------------------------------------- #
# module-level env defaults (used as TenantSessionOverlay's default
# pin_locked / allowed_tenants collaborators)
# --------------------------------------------------------------------------- #


def test_pin_locked_from_env_defaults_false(monkeypatch):
    monkeypatch.delenv("NW_MCP_TENANT_PIN_LOCKED", raising=False)
    assert pin_locked_from_env() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "True", "ON"])
def test_pin_locked_from_env_truthy_values(monkeypatch, value):
    monkeypatch.setenv("NW_MCP_TENANT_PIN_LOCKED", value)
    assert pin_locked_from_env() is True


def test_pin_locked_from_env_false_value(monkeypatch):
    monkeypatch.setenv("NW_MCP_TENANT_PIN_LOCKED", "false")
    assert pin_locked_from_env() is False


def test_allowed_tenants_from_env_defaults_none(monkeypatch):
    monkeypatch.delenv("NW_MCP_ALLOWED_TENANTS", raising=False)
    assert allowed_tenants_from_env() is None


def test_allowed_tenants_from_env_parses_csv(monkeypatch):
    monkeypatch.setenv("NW_MCP_ALLOWED_TENANTS", "acme, globex ,")
    assert allowed_tenants_from_env() == frozenset({"acme", "globex"})


# --------------------------------------------------------------------------- #
# env pin / current selection
# --------------------------------------------------------------------------- #


def test_env_pin_defaults_to_none_and_is_settable():
    overlay = _overlay()
    assert overlay.env_pin is None
    overlay.set_env_pin("acme")
    assert overlay.env_pin == "acme"
    overlay.set_env_pin(None)
    assert overlay.env_pin is None


def test_selected_tenant_and_config_default_to_none():
    overlay = _overlay()
    assert overlay.selected_tenant_id is None
    assert overlay.selected_config_name is None


# --------------------------------------------------------------------------- #
# guardrails
# --------------------------------------------------------------------------- #


def test_assert_switch_allowed_raises_when_pin_locked():
    overlay = _overlay(pin_locked=True)
    with pytest.raises(ValueError, match="pin-locked|PIN_LOCKED|Tenant switch is disabled"):
        overlay.assert_switch_allowed()


def test_assert_switch_allowed_noop_when_not_locked():
    overlay = _overlay(pin_locked=False)
    overlay.assert_switch_allowed()  # no raise


def test_pin_locked_message_matches_module_constant():
    overlay = _overlay(pin_locked=True)
    with pytest.raises(ValueError) as exc_info:
        overlay.assert_switch_allowed()
    assert str(exc_info.value) == PIN_LOCKED_MESSAGE


def test_assert_tenant_allowed_raises_on_unknown_tenant():
    overlay = _overlay(tenants={"acme": {}})
    with pytest.raises(ValueError, match="Unknown tenant"):
        overlay.assert_tenant_allowed("globex")


def test_assert_tenant_allowed_raises_when_excluded_by_allowlist():
    overlay = _overlay(tenants={"acme": {}, "globex": {}}, allowed=frozenset({"acme"}))
    with pytest.raises(ValueError, match="not allowed"):
        overlay.assert_tenant_allowed("globex")


def test_assert_tenant_allowed_passes_when_known_and_allowed():
    overlay = _overlay(tenants={"acme": {}}, allowed=frozenset({"acme"}))
    overlay.assert_tenant_allowed("acme")  # no raise


def test_filter_allowed_tenants_passthrough_when_no_allowlist():
    overlay = _overlay()
    assert overlay.filter_allowed_tenants(["acme", "globex"]) == ["acme", "globex"]


def test_filter_allowed_tenants_narrows_to_allowlist():
    overlay = _overlay(allowed=frozenset({"acme"}))
    assert overlay.filter_allowed_tenants(["acme", "globex"]) == ["acme"]


# --------------------------------------------------------------------------- #
# set_selected_tenant / set_selected_config (trusted, unguarded pin — used by
# in-process embedders like agents.toolhive.InProcessMcpClient)
# --------------------------------------------------------------------------- #


def test_set_selected_tenant_bypasses_guardrails():
    # Neither known to the store nor allowlisted — select_tenant would raise,
    # set_selected_tenant must not.
    overlay = _overlay(tenants={}, pin_locked=True, allowed=frozenset({"other"}))
    overlay.set_selected_tenant("acme")
    assert overlay.selected_tenant_id == "acme"


def test_set_selected_tenant_does_not_touch_config():
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    overlay.set_selected_tenant("globex")
    assert overlay.selected_tenant_id == "globex"
    assert overlay.selected_config_name == "prod"  # unlike select_tenant, no clearing


def test_set_selected_config_bypasses_coverage_check():
    overlay = _overlay(tenants={"acme": {}})  # no connector has "prod"
    overlay.set_selected_config("prod")
    assert overlay.selected_config_name == "prod"


def test_set_selected_tenant_and_config_accept_none():
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    overlay.set_selected_tenant("acme")
    overlay.set_selected_config("prod")
    overlay.set_selected_tenant(None)
    overlay.set_selected_config(None)
    assert overlay.selected_tenant_id is None
    assert overlay.selected_config_name is None


# --------------------------------------------------------------------------- #
# select_tenant / select_config
# --------------------------------------------------------------------------- #


def test_select_tenant_sets_selection():
    overlay = _overlay(tenants={"acme": {}})
    overlay.select_tenant("acme")
    assert overlay.selected_tenant_id == "acme"


def test_select_tenant_raises_when_pin_locked():
    overlay = _overlay(tenants={"acme": {}}, pin_locked=True)
    with pytest.raises(ValueError):
        overlay.select_tenant("acme")
    assert overlay.selected_tenant_id is None


def test_select_tenant_raises_on_unknown_tenant():
    overlay = _overlay(tenants={})
    with pytest.raises(ValueError, match="Unknown tenant"):
        overlay.select_tenant("acme")


def test_select_tenant_clears_config_missing_on_new_tenant():
    overlay = _overlay(
        tenants={
            "acme": {"prod": ["c1"]},
            "globex": {},
        }
    )
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    assert overlay.selected_config_name == "prod"

    overlay.select_tenant("globex")
    assert overlay.selected_tenant_id == "globex"
    assert overlay.selected_config_name is None


def test_select_tenant_keeps_config_when_still_covered():
    overlay = _overlay(
        tenants={
            "acme": {"prod": ["c1"]},
            "globex": {"prod": ["c2"]},
        }
    )
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    overlay.select_tenant("globex")
    assert overlay.selected_config_name == "prod"


def test_select_config_sets_selection_and_returns_coverage():
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    have, missing = overlay.select_config("acme", "prod")
    assert overlay.selected_config_name == "prod"
    assert have == ["c1"]
    assert missing == ["c2"]


def test_select_config_raises_on_unknown_config():
    overlay = _overlay(tenants={"acme": {}})
    with pytest.raises(ValueError, match="Unknown config"):
        overlay.select_config("acme", "prod")
    assert overlay.selected_config_name is None


def test_clear_config_if_missing_is_noop_when_nothing_selected():
    overlay = _overlay(tenants={"acme": {}})
    overlay.clear_config_if_missing("acme")  # no raise, stays None
    assert overlay.selected_config_name is None


# --------------------------------------------------------------------------- #
# effective_tenant_id / pinned_tenant_id_or_none
# --------------------------------------------------------------------------- #


def test_effective_tenant_id_prefers_explicit_arg():
    overlay = _overlay(tenants={"acme": {}, "globex": {}})
    overlay.select_tenant("globex")
    resolved = overlay.effective_tenant_id(
        tenant_arg="acme", resolve_from_request=lambda: "globex"
    )
    assert resolved == "acme"


def test_effective_tenant_id_arg_still_goes_through_guardrails():
    overlay = _overlay(tenants={"acme": {}}, pin_locked=True)
    with pytest.raises(ValueError):
        overlay.effective_tenant_id(
            tenant_arg="acme", resolve_from_request=lambda: (_ for _ in ()).throw(AssertionError)
        )


def test_effective_tenant_id_prefers_selected_over_request():
    overlay = _overlay(tenants={"acme": {}})
    overlay.select_tenant("acme")
    resolved = overlay.effective_tenant_id(resolve_from_request=lambda: "should-not-be-used")
    assert resolved == "acme"


def test_effective_tenant_id_falls_back_to_request_resolution():
    overlay = _overlay(tenants={"acme": {}})
    resolved = overlay.effective_tenant_id(resolve_from_request=lambda: "acme")
    assert resolved == "acme"


def test_effective_tenant_id_falls_back_to_default_tenant_when_available():
    overlay = _overlay(tenants={DEFAULT_TENANT: {}})

    def raise_missing():
        raise ValueError("no tenant in request")

    resolved = overlay.effective_tenant_id(resolve_from_request=raise_missing)
    assert resolved == DEFAULT_TENANT


def test_effective_tenant_id_raises_when_default_not_in_store():
    overlay = _overlay(tenants={})

    def raise_missing():
        raise ValueError("no tenant in request")

    with pytest.raises(ValueError, match=MISSING_TENANT_SELECT_MESSAGE):
        overlay.effective_tenant_id(resolve_from_request=raise_missing)


def test_effective_tenant_id_raises_when_default_excluded_by_allowlist():
    overlay = _overlay(tenants={DEFAULT_TENANT: {}}, allowed=frozenset({"acme"}))

    def raise_missing():
        raise ValueError("no tenant in request")

    with pytest.raises(ValueError, match=MISSING_TENANT_SELECT_MESSAGE):
        overlay.effective_tenant_id(resolve_from_request=raise_missing)


def test_pinned_tenant_id_or_none_returns_none_instead_of_raising():
    overlay = _overlay(tenants={})

    def raise_missing():
        raise ValueError("no tenant in request")

    assert overlay.pinned_tenant_id_or_none(resolve_from_request=raise_missing) is None


def test_pinned_tenant_id_or_none_returns_resolved_value():
    overlay = _overlay(tenants={"acme": {}})
    overlay.select_tenant("acme")
    assert overlay.pinned_tenant_id_or_none(resolve_from_request=lambda: "unused") == "acme"


# --------------------------------------------------------------------------- #
# effective_config_name
# --------------------------------------------------------------------------- #


def test_effective_config_name_none_when_multitenancy_disabled(monkeypatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    assert overlay.effective_config_name() is None


def test_effective_config_name_returns_selected_when_multitenancy_enabled(monkeypatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    assert overlay.effective_config_name() == "prod"


def test_effective_config_name_none_when_nothing_selected(monkeypatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    overlay = _overlay(tenants={"acme": {}})
    assert overlay.effective_config_name() is None


def test_effective_config_name_arg_outranks_selected(monkeypatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "true")
    overlay = _overlay(tenants={"acme": {"prod": ["c1"], "staging": ["c1"]}})
    overlay.select_tenant("acme")
    overlay.select_config("acme", "prod")
    assert overlay.effective_config_name(config_arg="staging") == "staging"


def test_effective_config_name_ignores_arg_when_multitenancy_disabled(monkeypatch):
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    overlay = _overlay(tenants={"acme": {"prod": ["c1"]}})
    assert overlay.effective_config_name(config_arg="prod") is None
