#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""ConnectorFactory wiring of OAuth2AuthProvider.on_refresh_token_rotated.

Covers the gap where the factory built OAuth2AuthProvider instances without ever
passing a rotation-persistence hook, so refresh-token rotation (e.g. Entra) had
nowhere to go for any YAML-wired oauth2 connector.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bindings.factory import ConnectorFactory
from node_wire_runtime.secrets import EnvSecretProvider, OverlaySecretProvider, tenant_scoped_secret_key


def _factory(monkeypatch: pytest.MonkeyPatch) -> ConnectorFactory:
    monkeypatch.setattr(ConnectorFactory, "_instantiate", lambda self, record: MagicMock())
    return ConnectorFactory()


def test_default_tenant_rotation_callback_writes_bare_key_to_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(monkeypatch)
    cfg = {
        "auth": {
            "provider": "oauth2",
            "grant_method": "refresh_token",
            "token_url_secret": "MS_TOKEN_URL",
            "client_id_secret": "MS_CLIENT_ID",
            "client_secret_secret": "MS_CLIENT_SECRET",
            "refresh_token_secret": "MS_REFRESH_TOKEN",
        }
    }
    provider = factory._build_auth_provider(
        "microsoft_teams",
        cfg,
        secret_provider=EnvSecretProvider(),
    )

    assert provider._on_refresh_token_rotated is not None
    provider._on_refresh_token_rotated("new-rt")

    assert OverlaySecretProvider.instance().get_secret("MS_REFRESH_TOKEN") == "new-rt"


def test_named_tenant_rotation_callback_writes_scoped_key_to_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _factory(monkeypatch)
    cfg = {
        "auth": {
            "provider": "oauth2",
            "grant_method": "refresh_token",
            "token_url_secret": "MS_TOKEN_URL",
            "client_id_secret": "MS_CLIENT_ID",
            "refresh_token_secret": "MS_REFRESH_TOKEN",
        }
    }
    provider = factory._build_auth_provider(
        "microsoft_teams",
        cfg,
        secret_provider=EnvSecretProvider(),
        tenant_id="acme",
        config_name="prod",
    )

    provider._on_refresh_token_rotated("rotated-value")

    scoped = tenant_scoped_secret_key("acme", "microsoft_teams", "MS_REFRESH_TOKEN", config_name="prod")
    assert OverlaySecretProvider.instance().get_secret(scoped) == "rotated-value"


def test_rotation_callback_not_wired_for_non_refresh_token_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """private_key_jwt/client_secret_post have no refresh token to rotate."""
    factory = _factory(monkeypatch)
    cfg = {
        "auth": {
            "provider": "oauth2",
            "grant_method": "client_secret_post",
            "token_url_secret": "MS_TOKEN_URL",
            "client_id_secret": "MS_CLIENT_ID",
            "client_secret_secret": "MS_CLIENT_SECRET",
        }
    }
    provider = factory._build_auth_provider(
        "microsoft_teams",
        cfg,
        secret_provider=EnvSecretProvider(),
    )
    assert provider._on_refresh_token_rotated is None
