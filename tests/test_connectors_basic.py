from __future__ import annotations

import asyncio

from pydantic import BaseModel

from connectors.http_generic.logic import HttpGenericConnector
from connectors.http_generic.schema import HttpRequestInput, HttpResponseOutput
from connectors.smtp.logic import SmtpConnector
from connectors.smtp.schema import SmtpSendInput, SmtpSendOutput
from connectors.stripe.logic import StripeConnector
from runtime import ConnectorResponse, ErrorCategory, SecretProvider
from connectors import auto_register


class DummySecretProvider(SecretProvider):
    def __init__(self) -> None:
        self._store = {"STRIPE_API_KEY": "sk_test_dummy", "smtp_user": "user", "smtp_pass": "pass"}

    def get_secret(self, key: str) -> str:
        return self._store[key]


def test_auto_register_runs_without_error():
    imported = auto_register()
    assert any("http_generic.registration" in name for name in imported)
    assert any("google_drive.logic" in name for name in imported)


def test_http_connector_instantiation_only():
    connector = HttpGenericConnector(HttpRequestInput, HttpResponseOutput)
    assert connector.connector_id == "http_generic"
    assert connector.action == "request"


def test_smtp_connector_instantiation_only():
    connector = SmtpConnector(SmtpSendInput, SmtpSendOutput, secret_provider=DummySecretProvider())
    assert connector.connector_id == "smtp"
    assert connector.action == "send_email"


def test_stripe_connector_instantiation_only():
    connector = StripeConnector(secret_provider=DummySecretProvider())
    assert connector.connector_id == "stripe"
    assert connector.action == "charge"

