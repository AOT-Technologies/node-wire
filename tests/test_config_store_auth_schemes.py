#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""ConnectorConfigStore validation and redaction for the `auth_schemes` block."""

from __future__ import annotations

import pytest

from node_wire_runtime.config_store import ConfigStoreError, ConnectorConfigStore


def test_auth_schemes_must_be_an_object() -> None:
    store = ConnectorConfigStore()
    with pytest.raises(ConfigStoreError, match="auth_schemes"):
        store.create("__default__", "pet_store", {"name": "default", "auth_schemes": ["not", "a", "dict"]})


def test_auth_schemes_entry_must_be_an_object() -> None:
    store = ConnectorConfigStore()
    with pytest.raises(ConfigStoreError, match="auth_schemes"):
        store.create(
            "__default__",
            "pet_store",
            {"name": "default", "auth_schemes": {"petstore_auth": "not-a-dict"}},
        )


def test_auth_schemes_accepted_alongside_auth() -> None:
    """Default `auth` plus one extra named scheme is accepted."""
    store = ConnectorConfigStore()
    doc = {
        "name": "default",
        "auth": {"provider": "static_token", "secret_key": "PET_STORE_API_KEY"},
        "auth_schemes": {
            "petstore_auth": {
                "provider": "static_token",
                "secret_key": "PET_STORE_ACCESS_TOKEN",
                "host_supplied": True,
            }
        },
    }
    record = store.create("__default__", "pet_store", doc)
    assert record.raw["auth_schemes"]["petstore_auth"]["secret_key"] == "PET_STORE_ACCESS_TOKEN"


def test_auth_schemes_without_inline_secrets_are_not_redacted() -> None:
    """secret_key is a *reference* (bare logical name), not an inline secret value —
    same convention `auth` already follows — so it must pass through `get()` unmasked."""
    store = ConnectorConfigStore()
    doc = {
        "name": "default",
        "auth_schemes": {
            "petstore_auth": {"provider": "static_token", "secret_key": "PET_STORE_ACCESS_TOKEN"}
        },
    }
    store.create("__default__", "pet_store", doc)
    view = store.get("__default__", "pet_store", "default")
    assert view is not None
    assert view["auth_schemes"]["petstore_auth"]["secret_key"] == "PET_STORE_ACCESS_TOKEN"


def test_connector_without_auth_schemes_is_unaffected() -> None:
    """The overwhelming majority of connectors never set auth_schemes at all — must
    still validate and round-trip with zero behavior change."""
    store = ConnectorConfigStore()
    doc = {"name": "default", "auth": {"provider": "static_token", "secret_key": "STRIPE_API_KEY"}}
    record = store.create("__default__", "stripe", doc)
    assert "auth_schemes" not in record.raw
