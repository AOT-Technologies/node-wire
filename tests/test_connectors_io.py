"""Unit tests for SMTP, HTTP generic, and Stripe connectors with mocked I/O."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx

from connectors.http_generic.logic import HttpGenericConnector
from connectors.http_generic.schema import HttpRequestInput
from connectors.smtp.logic import SmtpConnector
from connectors.smtp.schema import SmtpSendInput
from connectors.stripe.logic import StripeConnector
from runtime.secrets import SecretProvider


class _MapSecrets(SecretProvider):
    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def get_secret(self, key: str) -> str:
        return self._m[key]


def test_smtp_internal_execute_calls_aiosmtplib_send() -> None:
    secrets = _MapSecrets({"SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"})

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        return (250, "OK")

    async def _run() -> None:
        with patch("connectors.smtp.logic.aiosmtplib.send", new=fake_send):
            c = SmtpConnector(secret_provider=secrets)
            inp = SmtpSendInput(
                host="localhost",
                port=1025,
                use_tls=False,
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            out = await c.internal_execute(inp, trace_id="t-1")
        assert out.sent is True

    asyncio.run(_run())


def test_http_generic_internal_execute() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = httpx.Headers({"X-Test": "1"})
    mock_resp.text = "response-body"

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, **kwargs: object) -> MagicMock:
            return mock_resp

    async def _run() -> None:
        with patch("connectors.http_generic.logic.httpx.AsyncClient", return_value=_FakeAsyncClient()):
            c = HttpGenericConnector()
            inp = HttpRequestInput(url="http://example.com/path", method="GET")
            out = await c.internal_execute(inp, trace_id="t-2")
        assert out.status_code == 200
        assert out.body == "response-body"

    asyncio.run(_run())


def test_stripe_charge_via_run() -> None:
    secrets = _MapSecrets({"stripe_api_key": "sk_test_dummy"})

    with patch("connectors.stripe.logic.stripe.Charge") as mock_charge:
        mock_charge.create.return_value = {"id": "ch_123", "receipt_url": "https://pay.example/r"}
        c = StripeConnector(secret_provider=secrets)

        async def _run() -> None:
            resp = await c.run(
                {
                    "action": "charge",
                    "amount": 1000,
                    "currency": "usd",
                    "source": "tok_visa",
                }
            )
            assert resp.success is True
            assert resp.data is not None
            assert resp.data.get("charge_id") == "ch_123"

        asyncio.run(_run())
        mock_charge.create.assert_called_once()
