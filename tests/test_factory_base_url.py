# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""connectors.yaml / config-document ``base_url`` overrides a RestConnector's baked default."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal, Type

import pytest
from pydantic import BaseModel

from bindings.factory import ConnectorFactory
from node_wire_runtime import RestConnector, RestResponseOutput, nw_action
from node_wire_runtime.config_store import ConfigStoreError


class _ProbeInput(BaseModel):
    action: Literal["probe"] = "probe"


class _BaseUrlProbeConnector(RestConnector):
    connector_id = "base_url_probe"
    default_base_url = "https://baked.example.test"
    output_model: ClassVar[Type[BaseModel]] = RestResponseOutput
    _nw_abstract_base = False

    @nw_action("probe")
    async def probe(self, params: _ProbeInput, *, trace_id: str) -> RestResponseOutput:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _allow_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_MCP_SCOPE_POLICY_DEFAULT", "allow")
    monkeypatch.setenv(
        "NW_REST_ALLOWED_HOSTS", "override.example.test,top.example.test,nested.example.test"
    )


async def test_connectors_yaml_base_url_overrides_the_baked_default(tmp_path: Path) -> None:
    """Regression: the YAML bootstrap stores base_url under `config`, which was ignored."""
    config = tmp_path / "connectors.yaml"
    config.write_text(
        "connectors:\n"
        "  base_url_probe:\n"
        "    enabled: true\n"
        "    exposed_via: [mcp]\n"
        "    base_url: https://override.example.test\n",
        encoding="utf-8",
    )
    factory = ConnectorFactory(config)
    factory.load()

    connector = await factory.get("base_url_probe")

    assert isinstance(connector, RestConnector)
    assert connector.resolve_base_url() == "https://override.example.test"


async def test_top_level_base_url_wins_over_config_block(tmp_path: Path) -> None:
    config = tmp_path / "connectors.yaml"
    config.write_text("connectors: {}\n", encoding="utf-8")
    factory = ConnectorFactory(config)
    factory.load()
    factory.store.create(
        "acme",
        "base_url_probe",
        {
            "name": "default",
            "base_url": "https://top.example.test",
            "config": {"base_url": "https://nested.example.test"},
        },
    )

    connector = await factory.get("base_url_probe", tenant_id="acme")

    assert isinstance(connector, RestConnector)
    assert connector.resolve_base_url() == "https://top.example.test"


async def test_without_override_the_baked_default_is_used(tmp_path: Path) -> None:
    config = tmp_path / "connectors.yaml"
    config.write_text(
        "connectors:\n  base_url_probe:\n    enabled: true\n    exposed_via: [mcp]\n",
        encoding="utf-8",
    )
    factory = ConnectorFactory(config)
    factory.load()

    connector = await factory.get("base_url_probe")

    assert isinstance(connector, RestConnector)
    assert connector.resolve_base_url() == "https://baked.example.test"


def test_yaml_base_url_off_the_allowlist_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override host must be in NW_REST_ALLOWED_HOSTS, YAML included."""
    monkeypatch.delenv("NW_REST_ALLOWED_HOSTS")
    config = tmp_path / "connectors.yaml"
    config.write_text(
        "connectors:\n"
        "  base_url_probe:\n"
        "    enabled: true\n"
        "    exposed_via: [mcp]\n"
        "    base_url: https://override.example.test\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigStoreError, match="base_url host is not allowed"):
        ConnectorFactory(config).load()
