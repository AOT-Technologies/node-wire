#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Unit tests for SMTP, HTTP generic, and Stripe connectors with mocked I/O."""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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


def test_smtp_internal_execute_calls_aiosmtplib_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "localhost")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    secrets = _MapSecrets({"SMTP_USERNAME": "u", "SMTP_PASSWORD": "p"})

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        return (250, "OK")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
            c = SmtpConnector(secret_provider=secrets)
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            out = await c.internal_execute(inp, trace_id="t-1")
        assert out.sent is True

    asyncio.run(_run())


def test_smtp_send_email_does_not_log_sender_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """L1 — from_email (PII) must never appear in any log record."""
    import logging

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "true")

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
        with (
            patch(
                "node_wire_http_generic.logic.httpx.AsyncClient", return_value=_FakeAsyncClient()
            ),
            patch(
                "node_wire_http_generic.logic._assert_safe_destination",
                new=AsyncMock(return_value=None),
            ),
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


# ---------------------------------------------------------------------------
# H-2 regression: SSRF resolve-and-validate at connection time.
# ---------------------------------------------------------------------------


def _fake_getaddrinfo(ip: str):
    """Return an async stand-in for ``loop.getaddrinfo`` that resolves to ``ip``."""

    async def _resolver(host, port, *args, **kwargs):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    return _resolver


@pytest.mark.parametrize(
    "resolved_ip",
    [
        "127.0.0.1",  # loopback via DNS name
        "10.1.2.3",  # RFC1918
        "169.254.169.254",  # cloud metadata
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
        "::1",  # IPv6 loopback
    ],
)
def test_http_blocks_dns_name_resolving_to_internal_ip(resolved_ip: str) -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        with patch.object(loop, "getaddrinfo", new=_fake_getaddrinfo(resolved_ip)):
            with pytest.raises(SsrfBlockedError):
                # A perfectly public-looking hostname that resolves internally.
                await _assert_safe_destination("http://totally-public.example.com/x")

    asyncio.run(_run())


def test_http_allows_public_resolved_ip() -> None:
    from node_wire_http_generic.logic import _assert_safe_destination

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        with patch.object(loop, "getaddrinfo", new=_fake_getaddrinfo("93.184.216.34")):
            # Must not raise.
            await _assert_safe_destination("https://example.com/path")

    asyncio.run(_run())


def test_http_egress_allowlist_blocks_unlisted_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    monkeypatch.setenv("NW_HTTP_GENERIC_ALLOWED_HOSTS", "api.allowed.example.com")

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        with patch.object(loop, "getaddrinfo", new=_fake_getaddrinfo("93.184.216.34")):
            with pytest.raises(SsrfBlockedError):
                await _assert_safe_destination("https://evil.example.com/x")
            # Listed host with a public IP is allowed.
            await _assert_safe_destination("https://api.allowed.example.com/x")

    asyncio.run(_run())


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
            patch(
                "node_wire_http_generic.logic._assert_safe_destination",
                new=AsyncMock(return_value=None),
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


# ---------------------------------------------------------------------------
# SSRF resolver edge cases and request exception logging
# ---------------------------------------------------------------------------


def test_assert_safe_destination_missing_host() -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    async def _run() -> None:
        with pytest.raises(SsrfBlockedError, match="host is missing"):
            await _assert_safe_destination("http:///path")

    asyncio.run(_run())


def test_assert_safe_destination_gaierror() -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    async def _run() -> None:
        loop = asyncio.get_event_loop()

        async def _failing_resolver(host, port, *args, **kwargs):
            raise socket.gaierror("Name or service not known")

        with patch.object(loop, "getaddrinfo", new=_failing_resolver):
            with pytest.raises(SsrfBlockedError, match="could not be resolved"):
                await _assert_safe_destination("http://nonexistent.example.invalid/x")

    asyncio.run(_run())


def test_assert_safe_destination_empty_getaddrinfo() -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    async def _run() -> None:
        loop = asyncio.get_event_loop()

        async def _empty_resolver(host, port, *args, **kwargs):
            return []

        with patch.object(loop, "getaddrinfo", new=_empty_resolver):
            with pytest.raises(SsrfBlockedError, match="could not be resolved"):
                await _assert_safe_destination("http://empty-dns.example.com/x")

    asyncio.run(_run())


def test_assert_safe_destination_unparsable_ip() -> None:
    from node_wire_http_generic.logic import SsrfBlockedError, _assert_safe_destination

    async def _run() -> None:
        loop = asyncio.get_event_loop()

        async def _bad_ip_resolver(host, port, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", port))]

        with patch.object(loop, "getaddrinfo", new=_bad_ip_resolver):
            with pytest.raises(SsrfBlockedError, match="unparsable address"):
                await _assert_safe_destination("http://bad-ip.example.com/x")

    asyncio.run(_run())


def test_sanitize_url_for_log_ipv6_and_invalid() -> None:
    from unittest.mock import patch

    from node_wire_http_generic.logic import _sanitize_url_for_log

    assert (
        _sanitize_url_for_log("https://[2001:db8::1]:8443/path?q=1")
        == "https://[2001:db8::1]:8443/path"
    )
    with patch("node_wire_http_generic.logic.urlsplit", side_effect=ValueError("bad url")):
        assert _sanitize_url_for_log("not-a-url") == "<invalid-url>"


def test_http_request_exception_before_response_logged() -> None:
    class _FailingAsyncClient:
        async def __aenter__(self) -> "_FailingAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, **kwargs: object) -> MagicMock:
            raise httpx.ConnectError("connection refused")

    async def _run() -> None:
        with (
            patch(
                "node_wire_http_generic.logic.httpx.AsyncClient",
                return_value=_FailingAsyncClient(),
            ),
            patch(
                "node_wire_http_generic.logic._assert_safe_destination",
                new=AsyncMock(return_value=None),
            ),
            patch("node_wire_http_generic.logic.logger.error") as mocked_error,
        ):
            c = HttpGenericConnector()
            inp = HttpRequestInput(url="https://example.com/path", method="GET")
            with pytest.raises(httpx.ConnectError):
                await c.internal_execute(inp, trace_id="t-err")

        mocked_error.assert_called_once()
        extra = mocked_error.call_args.kwargs.get("extra") or {}
        assert extra.get("error_type") == "ConnectError"

    asyncio.run(_run())
