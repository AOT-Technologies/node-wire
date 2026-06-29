#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import pytest

from node_wire_http_generic.logic import HttpGenericConnector
from node_wire_smtp.logic import SmtpConnector
from node_wire_stripe.logic import StripeConnector
from node_wire_runtime import BaseConnector, SecretProvider
from node_wire_runtime.connector_registry import auto_register


class DummySecretProvider(SecretProvider):
    def __init__(self) -> None:
        self._store = {"STRIPE_API_KEY": "sk_test_dummy", "smtp_user": "user", "smtp_pass": "pass"}

    def get_secret(self, key: str) -> str:
        return self._store[key]


def test_auto_register_runs_without_error(monkeypatch):
    monkeypatch.setenv(
        "NW_ALLOWED_CONNECTORS",
        "fhir_cerner,fhir_epic,google_drive,smtp,stripe,http_generic,salesforce,slack",
    )
    imported = auto_register()
    if not imported:
        pytest.skip(
            "importlib.metadata entry points for node_wire.connectors are empty; "
            "use `pip install -e .` to run this assertion."
        )
    assert any("http_generic.registration" in name for name in imported)
    assert any("google_drive.logic" in name for name in imported)


def test_http_connector_instantiation_only():
    connector = HttpGenericConnector()
    assert connector.connector_id == "http_generic"
    assert isinstance(connector, BaseConnector)


def test_smtp_connector_instantiation_only():
    connector = SmtpConnector(secret_provider=DummySecretProvider())
    assert connector.connector_id == "smtp"
    assert isinstance(connector, BaseConnector)


def test_stripe_connector_instantiation_only():
    connector = StripeConnector(secret_provider=DummySecretProvider())
    assert connector.connector_id == "stripe"
    assert connector.action == "execute"


def test_salesforce_connector_instantiation_only():
    store = {"salesforce_instance_url": "https://test.salesforce.com"}
    provider = type("Mock", (), {"get_secret": lambda s, k: store[k]})()
    connector = BaseConnector.get_registry()["salesforce"](secret_provider=provider)
    assert connector.connector_id == "salesforce"
    assert "create_lead" in connector._action_registry


_ALL_EIGHT_CONNECTOR_IDS = (
    "http_generic",
    "smtp",
    "stripe",
    "google_drive",
    "fhir_epic",
    "fhir_cerner",
    "salesforce",
    "slack",
)


def test_factory_loads_all_eight_connectors() -> None:
    from bindings.factory import ConnectorFactory

    factory = ConnectorFactory()
    factory.load()
    for connector_id in _ALL_EIGHT_CONNECTOR_IDS:
        assert factory.get_for_protocol(connector_id, "rest") is not None, connector_id
