# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""ConnectorFactory ``auth_provider_hook`` seam and apikey_query tenant scoping."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from bindings.factory import ConnectorFactory
from node_wire_runtime.auth import ApiKeyQueryAuthProvider, NoAuthProvider, StaticTokenAuthProvider
from node_wire_runtime.auth.base import AuthProvider
from node_wire_runtime.secrets import SecretNotFoundError, SecretProvider, TenantSecretProvider


class _DictSecrets(SecretProvider):
    def __init__(self, data: Dict[str, str]) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        if key not in self._data:
            raise SecretNotFoundError(key)
        return self._data[key]


class _Marker(AuthProvider):
    def __init__(self, auth_cfg: Dict[str, Any]) -> None:
        self.auth_cfg = auth_cfg

    async def get_headers(self) -> Dict[str, str]:
        return {"X-Marker": "1"}


def _factory(hook: Any = None) -> ConnectorFactory:
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = _DictSecrets({"DEMO_TOKEN": "t"})
    factory._auth_provider_hook = hook
    return factory


def test_hook_replaces_default_and_named_schemes() -> None:
    seen: list[tuple[str, str]] = []

    def hook(connector_id: str, auth_cfg: Dict[str, Any]) -> AuthProvider:
        seen.append((connector_id, auth_cfg.get("provider", "")))
        return _Marker(auth_cfg)

    factory = _factory(hook)
    cfg = {
        "auth": {"provider": "static_token", "secret_key": "DEMO_TOKEN"},
        "auth_schemes": {"q": {"provider": "apikey_query", "name": "k", "secret_key": "K"}},
    }
    default = factory._build_auth_provider("demo", cfg)
    extras = factory._build_extra_auth_providers("demo", cfg)

    assert isinstance(default, _Marker)
    assert isinstance(extras["q"], _Marker)
    assert extras["q"].auth_cfg["name"] == "k"
    assert seen == [("demo", "static_token"), ("demo", "apikey_query")]


def test_hook_returning_none_falls_back_to_builtin_providers() -> None:
    factory = _factory(lambda connector_id, auth_cfg: None)
    provider = factory._build_auth_provider(
        "demo", {"auth": {"provider": "static_token", "secret_key": "DEMO_TOKEN"}}
    )
    assert isinstance(provider, StaticTokenAuthProvider)
    assert isinstance(factory._build_auth_provider("demo", {}), NoAuthProvider)


def test_constructor_without_hook_keeps_builtin_behaviour(tmp_path: Any) -> None:
    config = tmp_path / "connectors.yaml"
    config.write_text("connectors: {}\n", encoding="utf-8")
    factory = ConnectorFactory(config)
    provider = factory._build_auth_provider(
        "demo",
        {"auth": {"provider": "apikey_query", "name": "key", "secret_key": "DEMO_TOKEN"}},
        secret_provider=_DictSecrets({"DEMO_TOKEN": "t"}),
    )
    assert isinstance(provider, ApiKeyQueryAuthProvider)


@pytest.mark.asyncio
async def test_apikey_query_reads_the_tenant_scoped_secret() -> None:
    """Regression: apikey_query used the factory-wide provider, ignoring tenant scoping."""
    base = _DictSecrets(
        {
            "DEMO_API_KEY": "global-key",
            "NW_ACME_DEMO_DEFAULT_DEMO_API_KEY": "acme-key",
        }
    )
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = base
    scoped = TenantSecretProvider(base, "acme", "demo", config_name="default")

    provider = factory._build_auth_provider(
        "demo",
        {"auth": {"provider": "apikey_query", "name": "api_key", "secret_key": "DEMO_API_KEY"}},
        secret_provider=scoped,
        tenant_id="acme",
        config_name="default",
    )

    assert await provider.get_query_params() == {"api_key": "acme-key"}
