#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Unit tests for SMTP, HTTP generic, and Stripe connectors with mocked I/O."""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from node_wire_http_generic.logic import HttpGenericConnector
from node_wire_http_generic.schema import HttpRequestInput
from node_wire_smtp.logic import SmtpConnector
from node_wire_smtp.schema import SmtpSendInput
from node_wire_stripe.logic import StripeConnector
from node_wire_runtime.secrets import SecretProvider


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
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
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


def test_smtp_send_email_does_not_log_sender_address() -> None:
    """L1 — from_email (PII) must never appear in any log record."""
    import logging

    secrets = _MapSecrets({"SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"})

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        return (250, "OK")

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.DEBUG)
    smtp_logger = logging.getLogger("connectors.smtp")
    # Lower the logger's own level so INFO records are not silently dropped
    # when the root logger is configured at WARNING (the pytest default).
    _prev_level = smtp_logger.level
    smtp_logger.setLevel(logging.DEBUG)
    smtp_logger.addHandler(handler)
    try:

        async def _run() -> None:
            with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
                c = SmtpConnector(secret_provider=secrets)
                inp = SmtpSendInput(
                    host="smtp.example.com",
                    port=587,
                    use_tls=True,
                    from_email="sender@private.example.com",
                    to=["recipient@other.example.com"],
                    subject="Test",
                    body="body",
                )
                await c.internal_execute(inp, trace_id="t-pii")

        asyncio.run(_run())
    finally:
        smtp_logger.removeHandler(handler)
        smtp_logger.setLevel(_prev_level)

    assert len(captured) >= 2, "Expected at least prepare + sent log records"
    for record in captured:
        # The full sender address must never appear anywhere in the serialised record.
        log_text = str(record.__dict__)
        assert "sender@private.example.com" not in log_text, (
            f"Sender PII leaked into log record: {log_text!r}"
        )
        # The domain-only hint MUST be present in at least the prepare record.
    domains = [r.__dict__.get("sender_domain") for r in captured if "sender_domain" in r.__dict__]
    assert all(d == "private.example.com" for d in domains), (
        f"Unexpected sender_domain values: {domains}"
    )


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
        with patch(
            "node_wire_http_generic.logic.httpx.AsyncClient", return_value=_FakeAsyncClient()
        ):
            c = HttpGenericConnector()
            inp = HttpRequestInput(url="http://example.com/path", method="GET")
            out = await c.internal_execute(inp, trace_id="t-2")
        assert out.status_code == 200
        assert out.body == "response-body"

    asyncio.run(_run())


def test_http_request_input_normalizes_method() -> None:
    parsed = HttpRequestInput(url="https://example.com/path", method=" post ")
    assert parsed.method == "POST"


def test_http_request_input_rejects_unsupported_method() -> None:
    with pytest.raises(ValidationError):
        HttpRequestInput(url="https://example.com/path", method="TRACE")


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://localhost/health",
        "http://LOCALHOST/health",
        "http://127.0.0.1/internal",
        "http://10.0.0.25/api",
        "http://0.0.0.0/debug",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/health",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://metadata.google.internal./computeMetadata/v1",
    ],
)
def test_http_request_input_rejects_internal_targets(blocked_url: str) -> None:
    with pytest.raises(ValidationError):
        HttpRequestInput(url=blocked_url, method="GET")


def test_http_request_input_allows_public_url() -> None:
    parsed = HttpRequestInput(url="https://example.com/path?q=1", method="GET")
    assert str(parsed.url) == "https://example.com/path?q=1"


def test_http_generic_logs_sanitized_url() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = httpx.Headers({})
    mock_resp.text = "ok"

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, **kwargs: object) -> MagicMock:
            return mock_resp

    async def _run() -> None:
        with (
            patch(
                "node_wire_http_generic.logic.httpx.AsyncClient", return_value=_FakeAsyncClient()
            ),
            patch("node_wire_http_generic.logic.logger.info") as mocked_info,
        ):
            c = HttpGenericConnector()
            inp = HttpRequestInput(
                url="https://user:pass@example.com/path?token=secret&patient=123",
                method="GET",
            )
            await c.internal_execute(inp, trace_id="t-log")
        for call in mocked_info.call_args_list:
            extra = call.kwargs.get("extra") or {}
            if "url" in extra:
                assert extra["url"] == "https://example.com/path"
                assert "secret" not in extra["url"]
                assert "user:pass" not in extra["url"]

    asyncio.run(_run())


def test_stripe_charge_via_run() -> None:
    secrets = _MapSecrets({"stripe_api_key": "sk_test_dummy"})

    with patch("node_wire_stripe.logic.stripe.Charge") as mock_charge:
        mock_charge.create.return_value = MagicMock(
            id="ch_123", receipt_url="https://pay.example/r", paid=True
        )
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
        mock_charge.create.assert_called_once_with(
            api_key="sk_test_dummy",
            amount=1000,
            currency="usd",
            source="tok_visa",
            customer=None,
            description=None,
            metadata=None,
            idempotency_key=ANY,
        )
