from __future__ import annotations

import asyncio

from pydantic import BaseModel

from node_wire_http_generic.logic import HttpGenericConnector
from node_wire_smtp.logic import SmtpConnector
from node_wire_stripe.logic import StripeConnector
from node_wire_runtime import ConnectorResponse, ErrorCategory, BaseConnector, SecretProvider
from node_wire_runtime.connector_registry import auto_register


class DummySecretProvider(SecretProvider):
    def __init__(self) -> None:
        self._store = {"STRIPE_API_KEY": "sk_test_dummy", "smtp_user": "user", "smtp_pass": "pass"}

    def get_secret(self, key: str) -> str:
        return self._store[key]


def test_auto_register_runs_without_error(monkeypatch):
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "fhir_cerner,fhir_epic,google_drive,smtp,stripe,http_generic")
    imported = auto_register()
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
