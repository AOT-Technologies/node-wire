#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""SMTP connector tests. aiosmtplib is replaced by small stubs, so no mail server is needed."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from typing import Any

import pytest
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPRecipientsRefused,
    SMTPResponseException,
    SMTPServerDisconnected,
    SMTPTimeoutError,
)
from aiosmtplib.response import SMTPResponse
from pydantic import ValidationError

from node_wire_runtime.auth.base import AuthProvider
from node_wire_smtp import logic
from node_wire_smtp.logic import SmtpConnector
from node_wire_smtp.normalizers import normalize_smtp_send_email
from node_wire_smtp.relay import SmtpRelayNotAllowedError, resolve_smtp_relay
from node_wire_smtp.schema import (
    CheckConnectionInput,
    GetServerCapabilitiesInput,
    ExpandListInput,
    PingInput,
    SendBulkInput,
    SendRawInput,
    SmtpSendInput,
    ValidateAddressSyntaxInput,
    VerifyAddressInput,
)

EHLO = "mail.example.com hello\nSIZE 35882577\nAUTH PLAIN LOGIN\nSTARTTLS\n8BITMIME\nPIPELINING"
SEND = {
    "action": "send_email",
    "from_email": "a@example.com",
    "to": ["b@example.com"],
    "subject": "Hi",
    "body": "Hello",
}


class Secrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get_secret(self, key: str) -> str:
        return self.values[key]


class Creds(AuthProvider):
    def __init__(self, username: str = "user", password: str = "pass") -> None:
        self.pair = (username, password)

    async def get_headers(self) -> dict[str, str]:
        return {}

    async def get_client_credentials(self) -> tuple[str, str]:
        return self.pair


class BrokenCreds(AuthProvider):
    async def get_headers(self) -> dict[str, str]:
        return {}

    async def get_client_credentials(self) -> tuple[str, str]:
        raise KeyError("SMTP_USERNAME")


class Wire:
    """What the stubs saw, and what they should do."""

    def __init__(self) -> None:
        self.sent: list[tuple[EmailMessage, dict[str, Any]]] = []
        self.clients: list[FakeSMTP] = []
        self.error: Exception | None = None
        self.quit_error: Exception | None = None
        self.bulk: list[tuple[EmailMessage, dict[str, Any]]] = []
        self.bulk_errors: dict[int, Exception] = {}
        self.command_error: Exception | None = None
        self.transport: dict[str, Any] = {}

    @property
    def message(self) -> EmailMessage:
        return self.sent[-1][0]

    @property
    def kw(self) -> dict[str, Any]:
        return self.sent[-1][1]


class FakeSMTP:
    wire: Wire
    ehlo_text = EHLO

    def __init__(self, **kw: Any) -> None:
        self.kw = kw
        self.calls: list[str] = []
        self.wire.clients.append(self)

    async def connect(self) -> SMTPResponse:
        if self.wire.error:
            raise self.wire.error
        self.calls.append("connect")
        return SMTPResponse(220, "mail.example.com ready")

    async def ehlo(self) -> SMTPResponse:
        self.calls.append("ehlo")
        return SMTPResponse(250, self.ehlo_text)

    async def noop(self) -> SMTPResponse:
        self.calls.append("noop")
        return SMTPResponse(250, "OK")

    async def send_message(self, message: EmailMessage, **kw: Any) -> tuple[dict[str, Any], str]:
        error = self.wire.bulk_errors.get(len(self.wire.bulk))
        self.wire.bulk.append((message, kw))
        if error:
            raise error
        return {}, "2.0.0 OK"

    async def vrfy(self, address: str) -> SMTPResponse:
        if self.wire.command_error:
            raise self.wire.command_error
        self.calls.append(f"vrfy {address}")
        return SMTPResponse(252, "Cannot VRFY user, but will accept message")

    async def expn(self, name: str) -> SMTPResponse:
        self.calls.append(f"expn {name}")
        return SMTPResponse(250, "Ann <ann@example.com>\nBob <bob@example.com>")

    async def help(self) -> str:
        return "Commands: HELO EHLO MAIL RCPT DATA"

    def get_transport_info(self, key: str) -> Any:
        return self.wire.transport.get(key)

    async def quit(self) -> None:
        self.calls.append("quit")
        if self.wire.quit_error:
            raise self.wire.quit_error

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    # The full suite loads .env, so clear everything these tests set or read.
    for name in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USE_TLS",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "FROM_EMAIL",
        "NW_SMTP_ALLOWED_HOSTS",
        "NW_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SMTP_HOST", "relay.example.com")
    w = Wire()

    async def fake_send(message: EmailMessage, **kw: Any) -> tuple[dict[str, Any], str]:
        if w.error:
            raise w.error
        w.sent.append((message, kw))
        return {}, "2.0.0 OK"

    FakeSMTP.wire = w
    monkeypatch.setattr(logic.aiosmtplib, "send", fake_send)
    monkeypatch.setattr(logic.aiosmtplib, "SMTP", FakeSMTP)
    return w


def make(**kw: Any) -> SmtpConnector:
    kw.setdefault("secret_provider", Secrets())
    kw.setdefault("auth_provider", Creds())
    return SmtpConnector(**kw)


# ---- send_email ------------------------------------------------------------------------------


async def test_send_email_minimal(wire: Wire) -> None:
    result = await make().run(SEND)
    assert result.success
    assert result.data["sent"] is True
    assert result.data["message_id"] == wire.message["Message-ID"]
    assert wire.message["To"] == "b@example.com"
    assert wire.message["Date"]
    assert wire.kw["mail_options"] is None and wire.kw["rcpt_options"] is None
    assert (wire.kw["hostname"], wire.kw["port"]) == ("relay.example.com", 587)
    assert (wire.kw["username"], wire.kw["password"]) == ("user", "pass")
    assert wire.kw["use_tls"] is False and wire.kw["start_tls"] is True


async def test_send_email_all_options(wire: Wire) -> None:
    pdf = base64.b64encode(b"%PDF-1.4 test").decode()
    result = await make().run(
        {
            **SEND,
            "to": "Bob <b@example.com>",
            "cc": ["c@example.com"],
            "bcc": ["d@example.com"],
            "reply_to": "Reply <r@example.com>",
            "html_body": "<p>Hello</p>",
            "attachments": [
                {"filename": "a.pdf", "content_base64": pdf, "content_type": "application/pdf"}
            ],
            "headers": {"X-Campaign": "spring"},
            "priority": "high",
            "dsn_notify": ["SUCCESS", "FAILURE"],
            "dsn_return": "HDRS",
        }
    )
    assert result.success
    m = wire.message
    assert (m["To"], m["Cc"], m["Bcc"], m["Reply-To"]) == (
        "b@example.com",
        "c@example.com",
        "d@example.com",
        "r@example.com",
    )
    assert (m["X-Priority"], m["Importance"], m["X-Campaign"]) == ("1", "high", "spring")
    assert m.get_body(("html",)).get_content().strip() == "<p>Hello</p>"
    attachment = next(m.iter_attachments())
    assert attachment.get_filename() == "a.pdf"
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == b"%PDF-1.4 test"
    assert wire.kw["mail_options"] == ["RET=HDRS"]
    assert wire.kw["rcpt_options"] == ["NOTIFY=SUCCESS,FAILURE"]


@pytest.mark.parametrize(
    ("priority", "x", "importance"), [("normal", "3", "normal"), ("low", "5", "low")]
)
async def test_priority_values(wire: Wire, priority: str, x: str, importance: str) -> None:
    await make().run({**SEND, "priority": priority})
    assert (wire.message["X-Priority"], wire.message["Importance"]) == (x, importance)


async def test_relay_without_login_sends_no_credentials(wire: Wire) -> None:
    from node_wire_runtime.auth import NoAuthProvider

    result = await make(auth_provider=NoAuthProvider()).run(SEND)
    assert result.success
    assert (wire.kw["username"], wire.kw["password"]) == (None, None)


async def test_credentials_fall_back_to_the_secret_store_then_the_environment(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    from node_wire_runtime.auth import NoAuthProvider

    await make(
        auth_provider=NoAuthProvider(),
        secret_provider=Secrets({"SMTP_USERNAME": "su", "SMTP_PASSWORD": "sp"}),
    ).run(SEND)
    assert (wire.kw["username"], wire.kw["password"]) == ("su", "sp")
    monkeypatch.setenv("SMTP_USERNAME", "eu")
    monkeypatch.setenv("SMTP_PASSWORD", "ep")
    await make(auth_provider=NoAuthProvider()).run(SEND)
    assert (wire.kw["username"], wire.kw["password"]) == ("eu", "ep")


async def test_missing_credentials_are_an_auth_error(wire: Wire) -> None:
    result = await make(auth_provider=BrokenCreds()).run(SEND)
    assert (result.error_code, result.error_category) == ("SMTP_CREDENTIALS_MISSING", "AUTH")
    assert "SMTP_USERNAME" in result.message
    assert wire.sent == []


@pytest.mark.parametrize(
    ("port", "use_tls", "tls", "start"),
    [("465", "false", True, False), ("587", "true", False, True), ("25", "false", False, False)],
)
async def test_tls_mode_follows_port_and_setting(
    wire: Wire, monkeypatch: pytest.MonkeyPatch, port: str, use_tls: str, tls: bool, start: bool
) -> None:
    monkeypatch.setenv("SMTP_PORT", port)
    monkeypatch.setenv("SMTP_USE_TLS", use_tls)
    await make().run(SEND)
    assert (wire.kw["use_tls"], wire.kw["start_tls"]) == (tls, start)


async def test_timeout_comes_from_nw_timeout(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_TIMEOUT", "7")
    await make().run(SEND)
    assert wire.kw["timeout"] == 7.0


@pytest.mark.parametrize(
    ("error", "code", "category"),
    [
        (SMTPConnectError("refused"), "SMTP_CONNECT_ERROR", "RETRYABLE"),
        (SMTPTimeoutError("slow"), "SMTP_TIMEOUT", "RETRYABLE"),
        (SMTPAuthenticationError(535, "bad login"), "SMTP_AUTH_ERROR", "AUTH"),
        (SMTPRecipientsRefused([]), "SMTP_ERROR", "BUSINESS"),
        (SMTPServerDisconnected("gone"), "SMTP_ERROR", "BUSINESS"),
    ],
)
async def test_errors_are_classified(
    wire: Wire, error: Exception, code: str, category: str
) -> None:
    wire.error = error
    result = await make().run(SEND)
    assert (result.error_code, result.error_category) == (code, category)


async def test_a_relay_off_the_allowlist_is_refused_before_sending(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", "smtp.other.com")
    result = await make().run(SEND)
    assert result.error_code == "SMTP_RELAY_NOT_ALLOWED"
    assert wire.sent == []


async def test_secrets_and_addresses_are_not_logged(
    wire: Wire, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    wire.error = None
    await make(auth_provider=Creds("the-user", "the-secret")).run(
        {**SEND, "subject": "private subject", "body": "private body"}
    )
    wire.error = SMTPConnectError("refused")
    await make(auth_provider=Creds("the-user", "the-secret")).run(SEND)
    text = " ".join(str(r.__dict__) + r.getMessage() for r in caplog.records)
    for hidden in (
        "the-secret",
        "the-user",
        "a@example.com",
        "b@example.com",
        "private subject",
        "private body",
    ):
        assert hidden not in text
    # The sender's domain is logged, as its own field.
    assert {r.__dict__.get("sender_domain") for r in caplog.records} >= {"example.com"}


# ---- input schema ----------------------------------------------------------------------------


def test_relay_fields_in_the_request_are_dropped() -> None:
    inp = SmtpSendInput.model_validate(
        {**SEND, "host": "evil.example.com", "port": 25, "use_tls": False}
    )
    assert not hasattr(inp, "host") and inp.to == ["b@example.com"]


def test_alias_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        SmtpSendInput.model_validate(
            {**SEND, "from_email": None, "from": "x@example.com"}
        ).from_email
        == "x@example.com"
    )
    monkeypatch.setenv("FROM_EMAIL", "default@example.com")
    blank = {k: v for k, v in SEND.items() if k != "from_email"}
    assert SmtpSendInput.model_validate(blank).from_email == "default@example.com"
    assert (
        SmtpSendInput.model_validate({**SEND, "from_email": "system_default"}).from_email
        == "default@example.com"
    )
    assert (
        SmtpSendInput.model_validate({**SEND, "from_email": "Ann <ann@example.com>"}).from_email
        == "ann@example.com"
    )
    monkeypatch.delenv("FROM_EMAIL")
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    with pytest.raises(ValidationError, match="from_email is required"):
        SmtpSendInput.model_validate(blank)
    monkeypatch.setenv("SMTP_USERNAME", "login@example.com")
    assert SmtpSendInput.model_validate(blank).from_email == "login@example.com"


@pytest.mark.parametrize(
    "bad",
    [
        {"subject": "a\nBcc: x@example.com"},
        {"headers": {"Subject": "x"}},
        {"headers": {"Bcc": "x@example.com"}},
        {"headers": {"X Bad": "x"}},
        {"headers": {"X-Ok": "a\r\nBcc: x"}},
        {"attachments": [{"filename": "a.txt", "content_base64": "not base64!!"}]},
        {"attachments": [{"filename": "../a.txt", "content_base64": "YQ=="}]},
        {"attachments": [{"filename": "a\n.txt", "content_base64": "YQ=="}]},
        {
            "attachments": [
                {"filename": "a.txt", "content_base64": "YQ==", "content_type": "nonsense"}
            ]
        },
        {"dsn_notify": ["NEVER", "FAILURE"]},
        {"dsn_return": "ALL"},
        {"priority": "urgent"},
        {"to": [], "cc": None, "bcc": None},
    ],
)
def test_bad_input_is_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SmtpSendInput.model_validate({**SEND, **bad})


def test_non_dict_input_is_left_to_pydantic() -> None:
    with pytest.raises(ValidationError):
        SmtpSendInput.model_validate("not a dict")


def test_mcp_normalizer_maps_aliases_and_wraps_strings() -> None:
    args: dict[str, Any] = {
        "sender": "s@example.com",
        "to": "a@example.com",
        "cc": "c@example.com",
        "bcc": ["d@example.com"],
        "host": "x",
    }
    normalize_smtp_send_email(args)
    assert args == {
        "from_email": "s@example.com",
        "to": ["a@example.com"],
        "cc": ["c@example.com"],
        "bcc": ["d@example.com"],
    }


# ---- relay configuration ---------------------------------------------------------------------


def test_relay_comes_from_connectors_yaml_when_the_environment_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("SMTP_HOST", "SMTP_PORT", "SMTP_USE_TLS", "NW_SMTP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    relay = resolve_smtp_relay({"host": "smtp.yaml.com", "port": 2525, "use_tls": False})
    assert (relay.host, relay.port, relay.use_tls) == ("smtp.yaml.com", 2525, False)
    defaults = resolve_smtp_relay()
    assert (defaults.host, defaults.port, defaults.use_tls) == ("smtp.gmail.com", 587, True)


def test_the_environment_wins_over_connectors_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "smtp.env.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USE_TLS", "off")
    relay = resolve_smtp_relay({"host": "smtp.yaml.com", "port": 2525, "use_tls": True})
    assert (relay.host, relay.port, relay.use_tls) == ("smtp.env.com", 465, False)


def test_relay_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_HOST", "")
    with pytest.raises(SmtpRelayNotAllowedError, match="SMTP_HOST is empty"):
        resolve_smtp_relay()
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "nope")
    with pytest.raises(SmtpRelayNotAllowedError, match="port is invalid"):
        resolve_smtp_relay()
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", " , ")
    assert resolve_smtp_relay().host == "smtp.example.com"  # a blank list means no allowlist
    monkeypatch.setenv("NW_SMTP_ALLOWED_HOSTS", "SMTP.OTHER.COM.")
    with pytest.raises(SmtpRelayNotAllowedError, match="allowed hosts"):
        resolve_smtp_relay()


async def test_the_connector_uses_its_own_config(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SMTP_HOST")
    await make(config={"host": "smtp.yaml.com", "port": 2525, "use_tls": "false"}).run(SEND)
    assert (wire.kw["hostname"], wire.kw["port"], wire.kw["start_tls"]) == (
        "smtp.yaml.com",
        2525,
        False,
    )


# ---- connection actions ----------------------------------------------------------------------


async def test_check_connection(wire: Wire) -> None:
    result = await make().run({"action": "check_connection"})
    assert result.success
    assert result.data == {
        "connected": True,
        "authenticated": True,
        "tls": "starttls",
        "banner": "mail.example.com ready",
    }
    client = wire.clients[-1]
    assert (client.kw["hostname"], client.kw["username"], client.kw["password"]) == (
        "relay.example.com",
        "user",
        "pass",
    )
    assert client.calls == ["connect", "noop", "quit", "close"]


async def test_check_connection_without_login_and_implicit_tls(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    from node_wire_runtime.auth import NoAuthProvider

    monkeypatch.setenv("SMTP_PORT", "465")
    result = await make(auth_provider=NoAuthProvider()).run({"action": "check_connection"})
    assert result.data["authenticated"] is False and result.data["tls"] == "implicit"
    assert wire.clients[-1].kw["use_tls"] is True


async def test_check_connection_without_tls(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    result = await make().run({"action": "check_connection"})
    assert result.data["tls"] == "none"


async def test_check_connection_failure_is_classified(wire: Wire) -> None:
    wire.error = SMTPConnectError("refused")
    result = await make().run({"action": "check_connection"})
    assert (result.error_code, result.error_category) == ("SMTP_CONNECT_ERROR", "RETRYABLE")


async def test_a_failing_quit_does_not_hide_the_result(wire: Wire) -> None:
    wire.quit_error = SMTPServerDisconnected("gone")
    assert (await make().run({"action": "ping"})).success
    assert wire.clients[-1].calls[-1] == "close"


async def test_get_server_capabilities_reads_ehlo_without_logging_in(wire: Wire) -> None:
    result = await make().run({"action": "get_server_capabilities"})
    assert result.success
    assert result.data == {
        "banner": "mail.example.com ready",
        "tls": "starttls",
        "extensions": {
            "SIZE": "35882577",
            "AUTH": "PLAIN LOGIN",
            "STARTTLS": "",
            "8BITMIME": "",
            "PIPELINING": "",
        },
        "auth_methods": ["PLAIN", "LOGIN"],
        "max_message_size": 35882577,
    }
    assert (wire.clients[-1].kw["username"], wire.clients[-1].kw["password"]) == (None, None)


async def test_get_server_capabilities_with_a_bare_server(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeSMTP, "ehlo_text", "bare.example.com")
    result = await make().run({"action": "get_server_capabilities"})
    assert result.data["extensions"] == {} and result.data["auth_methods"] == []
    assert result.data["max_message_size"] is None


async def test_ping(wire: Wire) -> None:
    result = await make().run({"action": "ping"})
    assert result.data == {"code": 250, "message": "OK"}
    assert (wire.clients[-1].kw["username"], wire.clients[-1].kw["password"]) == (None, None)


@pytest.mark.parametrize("model", [CheckConnectionInput, GetServerCapabilitiesInput, PingInput])
def test_connection_actions_take_no_host_or_port(model: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"host": "evil.example.com"})


class FakeSSL:
    def __init__(
        self, cipher: tuple[str, str, int] | None = ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)
    ) -> None:
        self._cipher = cipher

    def version(self) -> str:
        return "TLSv1.3"

    def cipher(self) -> tuple[str, str, int] | None:
        return self._cipher


CERT = {
    "subject": ((("commonName", "mail.example.com"),), (("organizationName", "Example"),)),
    "issuer": ((("commonName", "Example CA"),),),
    "notBefore": "Jan  1 00:00:00 2026 GMT",
    "notAfter": "Jan  1 00:00:00 2027 GMT",
    "subjectAltName": (("DNS", "mail.example.com"), ("DNS", "smtp.example.com")),
}


def item(subject: str = "s", **kw: Any) -> dict[str, Any]:
    return {
        "from_email": "a@example.com",
        "to": ["b@example.com"],
        "subject": subject,
        "body": "b",
        **kw,
    }


# ---- send_bulk -------------------------------------------------------------------------------


async def test_send_bulk_uses_one_connection_and_reports_each_message(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "send_bulk",
            "messages": [
                item("one"),
                item("two", html_body="<p>x</p>", dsn_return="FULL"),
                item("three"),
            ],
        }
    )
    assert result.success
    assert (result.data["sent"], result.data["failed"]) == (3, 0)
    assert [r["index"] for r in result.data["results"]] == [0, 1, 2]
    assert result.data["results"][0]["message_id"] == wire.bulk[0][0]["Message-ID"]
    assert [m["Subject"] for m, _ in wire.bulk] == ["one", "two", "three"]
    assert wire.bulk[1][1] == {"mail_options": ["RET=FULL"], "rcpt_options": None}
    assert len(wire.clients) == 1
    assert (wire.clients[0].kw["username"], wire.clients[0].kw["password"]) == ("user", "pass")
    assert wire.clients[0].calls == ["connect", "quit", "close"]


async def test_send_bulk_keeps_going_after_a_refused_message(wire: Wire) -> None:
    wire.bulk_errors = {1: SMTPRecipientsRefused([])}
    result = await make().run(
        {"action": "send_bulk", "messages": [item("a"), item("b"), item("c")]}
    )
    assert result.success
    assert (result.data["sent"], result.data["failed"]) == (2, 1)
    failed = result.data["results"][1]
    assert failed["sent"] is False and failed["message_id"] is None and failed["error"]


async def test_send_bulk_reports_the_rest_when_the_connection_drops(wire: Wire) -> None:
    wire.bulk_errors = {1: SMTPServerDisconnected("connection lost")}
    result = await make().run(
        {"action": "send_bulk", "messages": [item("a"), item("b"), item("c")]}
    )
    assert (result.data["sent"], result.data["failed"]) == (1, 2)
    assert [r["sent"] for r in result.data["results"]] == [True, False, False]
    assert "connection lost" in result.data["results"][2]["error"]


@pytest.mark.parametrize(
    "error",
    [SMTPTimeoutError("timed out"), ConnectionResetError("reset"), OSError("broken pipe")],
    ids=["timeout", "reset", "oserror"],
)
async def test_send_bulk_returns_partial_results_on_a_timeout_or_transport_error(
    wire: Wire, error: Exception
) -> None:
    wire.bulk_errors = {1: error}
    result = await make().run(
        {"action": "send_bulk", "messages": [item("a"), item("b"), item("c")]}
    )
    assert result.success  # the first message went out, so the caller still gets its result
    assert (result.data["sent"], result.data["failed"]) == (1, 2)
    assert [r["sent"] for r in result.data["results"]] == [True, False, False]
    assert str(error) in result.data["results"][2]["error"]


async def test_send_bulk_connect_error_is_classified(wire: Wire) -> None:
    wire.error = SMTPConnectError("refused")
    result = await make().run({"action": "send_bulk", "messages": [item()]})
    assert (result.error_code, result.error_category) == ("SMTP_CONNECT_ERROR", "RETRYABLE")


@pytest.mark.parametrize(
    "messages",
    [[], [item("x") for _ in range(101)], [{**item(), "subject": "a\nb"}], [{**item(), "to": []}]],
)
def test_send_bulk_input_is_checked(messages: list[Any]) -> None:
    with pytest.raises(ValidationError):
        SendBulkInput.model_validate({"messages": messages})


# ---- send_raw --------------------------------------------------------------------------------

RAW = "From: a@example.com\r\nTo: b@example.com\r\nSubject: Raw\r\nMessage-ID: <raw-1@example.com>\r\n\r\nbody"


async def test_send_raw_sends_the_message_as_given(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "send_raw",
            "from_email": "Ann <a@example.com>",
            "recipients": ["b@example.com", "Cy <c@example.com>"],
            "message": RAW,
        }
    )
    assert result.data == {"sent": True, "message_id": "<raw-1@example.com>"}
    message, kw = wire.sent[-1]
    assert message == RAW
    assert (kw["sender"], kw["recipients"]) == ("a@example.com", ["b@example.com", "c@example.com"])
    assert (kw["hostname"], kw["username"], kw["password"]) == ("relay.example.com", "user", "pass")


async def test_send_raw_without_a_message_id(wire: Wire) -> None:
    result = await make().run(
        {
            "action": "send_raw",
            "from_email": "a@example.com",
            "recipients": "b@example.com",
            "message": "From: a@example.com\r\nSubject: x\r\n\r\nhi",
        }
    )
    assert result.data["message_id"] is None


async def test_send_raw_errors_are_classified(wire: Wire) -> None:
    wire.error = SMTPAuthenticationError(535, "bad login")
    result = await make().run(
        {
            "action": "send_raw",
            "from_email": "a@example.com",
            "recipients": ["b@example.com"],
            "message": RAW,
        }
    )
    assert (result.error_code, result.error_category) == ("SMTP_AUTH_ERROR", "AUTH")


@pytest.mark.parametrize(
    "bad",
    [{"recipients": []}, {"message": ""}, {"from_email": "nope"}, {"host": "evil.example.com"}],
)
def test_send_raw_input_is_checked(bad: dict[str, Any]) -> None:
    good = {"from_email": "a@example.com", "recipients": ["b@example.com"], "message": RAW}
    with pytest.raises(ValidationError):
        SendRawInput.model_validate({**good, **bad})


def raw_with(*lines: str, body: str = "hi") -> str:
    return "\r\n".join(lines) + "\r\n\r\n" + body


@pytest.mark.parametrize(
    ("message", "problem"),
    [
        (raw_with("Subject: no sender"), "exactly one From"),
        (raw_with("From: other@example.com"), "exactly one From"),
        (raw_with("From: a@example.com", "From: a@example.com"), "exactly one From"),
        (raw_with("From: a@example.com, b@example.com"), "exactly one From"),
        (raw_with("From: a@example.com", "Bcc: hidden@example.com"), "Bcc"),
        (raw_with("From: a@example.com", "bcc: hidden@example.com"), "Bcc"),
        (raw_with("From: a@example.com", "Received: from evil.example.com"), "Received"),
        (raw_with("From: a@example.com", "Return-Path: <x@example.com>"), "Return-Path"),
    ],
)
def test_send_raw_message_is_checked(message: str, problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        SendRawInput.model_validate(
            {"from_email": "a@example.com", "recipients": ["b@example.com"], "message": message}
        )


def test_send_raw_message_size_is_capped() -> None:
    # Built here, not as a test parameter: pytest puts parameters in an environment variable.
    big = "From: a@example.com\r\n\r\n" + "x" * 10_000_000
    with pytest.raises(ValidationError, match="at most"):
        SendRawInput.model_validate(
            {"from_email": "a@example.com", "recipients": ["b@example.com"], "message": big}
        )


def test_send_raw_accepts_a_from_header_with_a_display_name_and_other_case() -> None:
    inp = SendRawInput.model_validate(
        {
            "from_email": "Ann@Example.com",
            "recipients": ["b@example.com"],
            "message": raw_with('From: "Ann A" <ann@example.COM>', "Reply-To: r@example.com"),
        }
    )
    assert inp.from_email == "Ann@example.com"


def test_an_attachment_is_capped() -> None:
    too_big = "A" * 14_000_001
    with pytest.raises(ValidationError):
        SmtpSendInput.model_validate(
            {**SEND, "attachments": [{"filename": "big.bin", "content_base64": too_big}]}
        )


# ---- get_tls_info ----------------------------------------------------------------------------


async def test_get_tls_info(wire: Wire) -> None:
    wire.transport = {"ssl_object": FakeSSL(), "peercert": CERT}
    result = await make().run({"action": "get_tls_info"})
    assert result.data == {
        "tls": "starttls",
        "version": "TLSv1.3",
        "cipher": "TLS_AES_256_GCM_SHA384",
        "cipher_bits": 256,
        "certificate": {
            "subject": "commonName=mail.example.com, organizationName=Example",
            "issuer": "commonName=Example CA",
            "not_before": "Jan  1 00:00:00 2026 GMT",
            "not_after": "Jan  1 00:00:00 2027 GMT",
            "subject_alt_names": ["mail.example.com", "smtp.example.com"],
        },
    }
    assert (wire.clients[-1].kw["username"], wire.clients[-1].kw["password"]) == (None, None)


async def test_get_tls_info_without_a_certificate_or_cipher(wire: Wire) -> None:
    wire.transport = {"ssl_object": FakeSSL(cipher=None)}
    result = await make().run({"action": "get_tls_info"})
    assert result.data["certificate"] is None
    assert (result.data["cipher"], result.data["cipher_bits"], result.data["version"]) == (
        None,
        None,
        "TLSv1.3",
    )


async def test_get_tls_info_on_a_plain_connection(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    result = await make().run({"action": "get_tls_info"})
    assert result.data == {
        "tls": "none",
        "version": None,
        "cipher": None,
        "cipher_bits": None,
        "certificate": None,
    }


# ---- verify_address, expand_list, get_help ---------------------------------------------------


async def test_verify_address(wire: Wire) -> None:
    result = await make().run({"action": "verify_address", "address": "ann@example.com"})
    assert result.data == {"code": 252, "message": "Cannot VRFY user, but will accept message"}
    client = wire.clients[-1]
    assert client.calls == ["connect", "vrfy ann@example.com", "quit", "close"]
    assert client.kw["username"] == "user"


async def test_verify_address_disabled_on_the_server_is_a_business_error(wire: Wire) -> None:
    wire.command_error = SMTPResponseException(502, "Command not implemented")
    result = await make().run({"action": "verify_address", "address": "ann@example.com"})
    assert (result.error_code, result.error_category) == ("SMTP_ERROR", "BUSINESS")


@pytest.mark.parametrize("value", ["", "   ", "a\r\nQUIT", "x" * 256])
def test_server_command_arguments_are_checked(value: str) -> None:
    with pytest.raises(ValidationError):
        VerifyAddressInput.model_validate({"address": value})
    with pytest.raises(ValidationError):
        ExpandListInput.model_validate({"list_name": value})


async def test_expand_list(wire: Wire) -> None:
    result = await make().run({"action": "expand_list", "list_name": "staff"})
    assert result.data["code"] == 250
    assert result.data["members"] == ["Ann <ann@example.com>", "Bob <bob@example.com>"]
    assert "expn staff" in wire.clients[-1].calls


async def test_get_help(wire: Wire) -> None:
    result = await make().run({"action": "get_help"})
    assert result.data == {"message": "Commands: HELO EHLO MAIL RCPT DATA"}
    assert wire.clients[-1].kw["username"] is None


# ---- validate_address_syntax -----------------------------------------------------------------


async def test_validate_address_syntax_is_offline(wire: Wire) -> None:
    long_local = "a" * 65 + "@example.com"
    result = await make().run(
        {
            "action": "validate_address_syntax",
            "addresses": [
                "Ann@Example.COM",
                "no-at-sign",
                long_local,
                "user@host.local",
                "a b@example.com",
            ],
        }
    )
    assert result.success
    ok, *bad = result.data["results"]
    assert ok == {
        "address": "Ann@Example.COM",
        "valid": True,
        "normalized": "Ann@example.com",
        "reason": None,
    }
    assert [b["valid"] for b in bad] == [False] * 4
    assert all(b["reason"] and b["normalized"] is None for b in bad)
    assert wire.clients == [] and wire.sent == []


@pytest.mark.parametrize("addresses", [[], ["a@example.com"] * 101])
def test_validate_address_syntax_input_is_checked(addresses: list[str]) -> None:
    with pytest.raises(ValidationError):
        ValidateAddressSyntaxInput.model_validate({"addresses": addresses})


def test_only_validate_address_syntax_skips_credentials() -> None:
    metas = SmtpConnector.nw_action_metas()
    assert {name for name, m in metas.items() if not m.requires_auth} == {"validate_address_syntax"}


def test_the_connector_registers_every_action() -> None:
    assert set(SmtpConnector.nw_action_metas()) == {
        "send_email",
        "send_bulk",
        "send_raw",
        "check_connection",
        "get_server_capabilities",
        "get_tls_info",
        "ping",
        "verify_address",
        "expand_list",
        "get_help",
        "validate_address_syntax",
    }
