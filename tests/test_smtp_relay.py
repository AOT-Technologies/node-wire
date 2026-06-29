#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from node_wire_smtp.logic import SmtpConnector
from node_wire_smtp.relay import SmtpRelayNotAllowedError, resolve_smtp_relay
from node_wire_smtp.schema import SmtpSendInput


def test_resolve_smtp_relay_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    relay = resolve_smtp_relay()
    assert relay.host == "smtp.example.com"
    assert relay.port == 2525
    assert relay.use_tls is False


def test_smtp_send_input_strips_relay_fields_from_payload() -> None:
    inp = SmtpSendInput(
        host="evil.example.com",
        port=25,
        use_tls=False,
        from_email="a@example.com",
        to=["a@example.com"],
        subject="s",
        body="b",
    )
    assert inp.to == ["a@example.com"]
    assert inp.subject == "s"
    assert inp.body == "b"


def test_resolve_smtp_relay_allowlist_blocks_unlisted_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", "sandbox.smtp.mailtrap.io")

    with pytest.raises(SmtpRelayNotAllowedError, match="allowed hosts"):
        resolve_smtp_relay()


def test_resolve_smtp_relay_allowlist_allows_listed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMTP_HOST", "sandbox.smtp.mailtrap.io")
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", "sandbox.smtp.mailtrap.io")

    relay = resolve_smtp_relay()
    assert relay.host == "sandbox.smtp.mailtrap.io"


def test_smtp_connector_uses_env_relay_not_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "pinned.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "true")

    captured: dict[str, object] = {}

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        captured.update(kwargs)
        return (250, "OK")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
            connector = SmtpConnector()
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            out = await connector.internal_execute(inp, trace_id="t-relay")
        assert out.sent is True

    asyncio.run(_run())
    assert captured["hostname"] == "pinned.example.com"
    assert captured["port"] == 587


def test_smtp_connector_rejects_allowlist_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "evil.example.com")
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", "smtp.gmail.com")

    send_called = False

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal send_called
        send_called = True
        return (250, "OK")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
            connector = SmtpConnector()
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            with pytest.raises(SmtpRelayNotAllowedError):
                await connector.internal_execute(inp, trace_id="t-blocked")

    asyncio.run(_run())
    assert send_called is False


def test_resolve_smtp_relay_empty_host_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "")

    with pytest.raises(SmtpRelayNotAllowedError, match="SMTP_HOST is empty"):
        resolve_smtp_relay()


def test_resolve_smtp_relay_invalid_port_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "not-a-port")

    with pytest.raises(SmtpRelayNotAllowedError, match="port is invalid"):
        resolve_smtp_relay()


def test_smtp_connector_uses_auth_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from node_wire_runtime.auth.base import AuthProvider

    class CredsAuthProvider(AuthProvider):
        async def get_client_credentials(self) -> tuple[str, str]:
            return ("auth-user", "auth-pass")

        async def get_headers(self) -> dict[str, str]:
            return {}

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USE_TLS", "false")

    captured: dict[str, object] = {}

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        captured.update(kwargs)
        return (250, "OK")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
            connector = SmtpConnector(auth_provider=CredsAuthProvider())
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            out = await connector.internal_execute(inp, trace_id="t-auth")
        assert out.sent is True

    asyncio.run(_run())
    assert captured["username"] == "auth-user"
    assert captured["password"] == "auth-pass"
    assert captured["use_tls"] is True
    assert captured["start_tls"] is False


def test_smtp_connector_send_failure_logs_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "true")

    async def failing_send(*args: object, **kwargs: object) -> tuple[int, str]:
        raise ConnectionError("smtp down")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=failing_send):
            connector = SmtpConnector()
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            with pytest.raises(ConnectionError, match="smtp down"):
                await connector.internal_execute(inp, trace_id="t-fail")

    asyncio.run(_run())


def test_smtp_connector_falls_back_to_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingSecrets:
        def get_secret(self, key: str) -> str:
            raise KeyError(key)

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("SMTP_USERNAME", "env-user")
    monkeypatch.setenv("SMTP_PASSWORD", "env-pass")

    captured: dict[str, object] = {}

    async def fake_send(*args: object, **kwargs: object) -> tuple[int, str]:
        captured.update(kwargs)
        return (250, "OK")

    async def _run() -> None:
        with patch("node_wire_smtp.logic.aiosmtplib.send", new=fake_send):
            connector = SmtpConnector(secret_provider=FailingSecrets())
            inp = SmtpSendInput(
                from_email="a@example.com",
                to=["b@example.com"],
                subject="s",
                body="hi",
            )
            out = await connector.internal_execute(inp, trace_id="t-env")
        assert out.sent is True

    asyncio.run(_run())
    assert captured["username"] == "env-user"
    assert captured["password"] == "env-pass"
