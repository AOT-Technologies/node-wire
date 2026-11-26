#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
# Version details
#   SMTP: RFC 5321 (SMTP), RFC 6409 (submission, port 587), RFC 8314 (TLS from the start, port 465),
#         RFC 3207 (STARTTLS), RFC 4954 (AUTH). Client library: aiosmtplib 5.x (Python 3.11+).
#   Relay: SMTP_HOST / SMTP_PORT / SMTP_USE_TLS, or host / port / use_tls in connectors.yaml.
#     Server-side only: no action accepts a host, a port or a credential.
#   Auth: the username and password come from the `static_credentials` auth provider
#     (SMTP_USERNAME / SMTP_PASSWORD secrets) and are only ever sent to the configured relay.
from __future__ import annotations

import base64
import contextlib
import logging
import os
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from email.parser import Parser
from typing import Any, AsyncIterator, ClassVar, Dict, List, Tuple, Type

import aiosmtplib
from email_validator import EmailNotValidError, validate_email

from node_wire_runtime import BaseConnector, ErrorCategory, nw_action

from .normalizers import normalize_smtp_send_email
from .relay import SmtpRelayConfig, SmtpRelayNotAllowedError, resolve_smtp_relay
from .schema import (
    AddressCheck,
    BulkResult,
    CheckConnectionInput,
    CheckConnectionOutput,
    ExpandListInput,
    ExpandListOutput,
    GetHelpInput,
    GetHelpOutput,
    GetServerCapabilitiesInput,
    GetServerCapabilitiesOutput,
    GetTlsInfoInput,
    GetTlsInfoOutput,
    PingInput,
    PingOutput,
    SendBulkInput,
    SendBulkOutput,
    SendRawInput,
    SendRawOutput,
    SmtpSendInput,
    SmtpSendOutput,
    ValidateAddressSyntaxInput,
    ValidateAddressSyntaxOutput,
    VerifyAddressInput,
    VerifyAddressOutput,
)

logger = logging.getLogger("connectors.smtp")

_PRIORITY = {"high": ("1", "high"), "normal": ("3", "normal"), "low": ("5", "low")}


def _build_message(params: SmtpSendInput) -> EmailMessage:
    sender = str(params.from_email)
    sender_domain = sender.split("@")[-1]
    to = [params.to] if isinstance(params.to, str) else list(params.to)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(str(a) for a in to)
    if params.cc:
        message["Cc"] = ", ".join(str(a) for a in params.cc)
    if params.bcc:  # aiosmtplib sends to Bcc recipients and drops the header from the message
        message["Bcc"] = ", ".join(str(a) for a in params.bcc)
    if params.reply_to:
        message["Reply-To"] = str(params.reply_to)
    message["Subject"] = params.subject
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid(domain=sender_domain)
    if params.priority:
        message["X-Priority"], message["Importance"] = _PRIORITY[params.priority]
    for name, value in (params.headers or {}).items():
        message[name] = value
    message.set_content(params.body)
    if params.html_body:
        message.add_alternative(params.html_body, subtype="html")
    for attachment in params.attachments or []:
        maintype, subtype = attachment.content_type.split("/", 1)
        message.add_attachment(
            base64.b64decode(attachment.content_base64),
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )
    return message


def _dsn_options(params: SmtpSendInput) -> Tuple[List[str] | None, List[str] | None]:
    """Delivery status notification options (RFC 3461): the server must advertise DSN."""
    mail_options = [f"RET={params.dsn_return}"] if params.dsn_return else None
    rcpt_options = [f"NOTIFY={','.join(params.dsn_notify)}"] if params.dsn_notify else None
    return mail_options, rcpt_options


def _dn(parts: Any) -> str:
    """A certificate name as `CN=..., O=...` from the tuple form ssl.getpeercert() uses."""
    return ", ".join(f"{k}={v}" for rdn in parts or () for k, v in rdn)


class SmtpCredentialsError(ValueError):
    """The auth provider could not supply the SMTP username and password."""


class SmtpAuthFailedError(Exception):
    """The relay refused the configured credentials."""


class SmtpConnectFailedError(ConnectionError):
    """Could not reach the relay: nothing was sent, so the call is retried."""


class SmtpTimeoutError(TimeoutError):
    """The relay stopped answering mid-conversation. The message may have been sent, so the
    call is not retried (a retry could send it twice)."""


class SmtpDisconnectedError(ConnectionError):
    """The relay dropped the connection mid-conversation; as for a timeout, not retried."""


class SmtpConnector(BaseConnector):
    """SMTP connector for sending email and inspecting the configured relay, via aiosmtplib."""

    connector_id = "smtp"
    output_model = SmtpSendOutput

    # Scoped to smtp only.
    # Simplified: a dropped connection (SMTPServerDisconnected) is not retried, because a send
    # may already have been accepted and a retry could deliver it twice.
    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        SmtpRelayNotAllowedError: (ErrorCategory.FATAL, "SMTP_RELAY_NOT_ALLOWED"),
        SmtpCredentialsError: (ErrorCategory.AUTH, "SMTP_CREDENTIALS_MISSING"),
        SmtpAuthFailedError: (ErrorCategory.AUTH, "SMTP_AUTH_ERROR"),
        SmtpConnectFailedError: (ErrorCategory.RETRYABLE, "SMTP_CONNECT_ERROR"),
        SmtpTimeoutError: (ErrorCategory.FATAL, "SMTP_TIMEOUT"),
        SmtpDisconnectedError: (ErrorCategory.FATAL, "SMTP_DISCONNECTED"),
        aiosmtplib.errors.SMTPConnectError: (ErrorCategory.RETRYABLE, "SMTP_CONNECT_ERROR"),
        aiosmtplib.errors.SMTPTimeoutError: (ErrorCategory.FATAL, "SMTP_TIMEOUT"),
        aiosmtplib.errors.SMTPAuthenticationError: (ErrorCategory.AUTH, "SMTP_AUTH_ERROR"),
        aiosmtplib.errors.SMTPException: (ErrorCategory.BUSINESS, "SMTP_ERROR"),
    }

    async def _credentials(self) -> Tuple[str | None, str | None]:
        """The username and password for the relay, or (None, None) for a relay without login."""
        try:
            creds = await self._auth_provider.get_client_credentials()
        except Exception as exc:  # noqa: BLE001
            raise SmtpCredentialsError(
                "SMTP credentials are not available: check the SMTP_USERNAME and SMTP_PASSWORD secrets"
            ) from exc
        if isinstance(creds, (list, tuple)) and len(creds) == 2:
            username, password = str(creds[0]), str(creds[1])
        else:
            try:
                username = self.secret_provider.get_secret("SMTP_USERNAME")
                password = self.secret_provider.get_secret("SMTP_PASSWORD")
            except Exception:  # noqa: BLE001
                username = os.environ.get("SMTP_USERNAME", "")
                password = os.environ.get("SMTP_PASSWORD", "")
        return username or None, password or None

    @staticmethod
    def _tls_mode(relay: SmtpRelayConfig) -> str:
        if relay.port == 465:
            return "implicit"
        return "starttls" if relay.use_tls else "none"

    @staticmethod
    def _timeout() -> float:
        return float(os.getenv("NW_TIMEOUT", "30.0"))

    @contextlib.asynccontextmanager
    async def _session(
        self, relay: SmtpRelayConfig, username: str | None = None, password: str | None = None
    ) -> AsyncIterator[Tuple[aiosmtplib.SMTP, str]]:
        """Connect to the relay (EHLO, STARTTLS and login as configured), yield the client and the
        server greeting, and always QUIT. One connection per call."""
        mode = self._tls_mode(relay)
        client = aiosmtplib.SMTP(
            hostname=relay.host,
            port=relay.port,
            username=username,
            password=password,
            use_tls=mode == "implicit",
            start_tls=mode == "starttls",
            timeout=self._timeout(),
        )
        greeting = await client.connect()
        try:
            yield client, str(greeting.message)
        finally:
            with contextlib.suppress(Exception):
                await client.quit()
            client.close()

    def _log(
        self, trace_id: str, action: str, relay: SmtpRelayConfig, message: str, **extra: object
    ) -> None:
        # Only the relay, counts and the sender's domain are logged: never addresses, subjects,
        # bodies or credentials.
        logger.info(
            message,
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": action,
                "host": relay.host,
                "port": relay.port,
                **extra,
            },
        )

    @nw_action("send_email", alias_tolerant=True, mcp_normalize=normalize_smtp_send_email)
    async def send_email(self, params: SmtpSendInput, *, trace_id: str) -> SmtpSendOutput:
        relay = resolve_smtp_relay(self.config)
        sender = str(params.from_email)
        sender_domain = sender.split("@")[-1] if "@" in sender else "unknown"
        to = [params.to] if isinstance(params.to, str) else list(params.to)
        self._log(
            trace_id,
            "send_email",
            relay,
            "Preparing SMTP message",
            sender_domain=sender_domain,
            recipient_count=len(to) + len(params.cc or []) + len(params.bcc or []),
        )

        username, password = await self._credentials()

        message = _build_message(params)
        mail_options, rcpt_options = _dsn_options(params)

        mode = self._tls_mode(relay)
        try:
            _, response = await aiosmtplib.send(
                message,
                hostname=relay.host,
                port=relay.port,
                username=username,
                password=password,
                use_tls=mode == "implicit",
                start_tls=mode == "starttls",
                timeout=self._timeout(),
                mail_options=mail_options,
                rcpt_options=rcpt_options,
            )
        except Exception as exc:  # noqa: BLE001
            # Log with relay context, then re-raise as a typed error the error_map classifies.
            logger.error(
                "SMTP send failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "send_email",
                    "host": relay.host,
                    "port": relay.port,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            where = f"{relay.host}:{relay.port}"
            if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
                raise SmtpAuthFailedError(
                    f"SMTP authentication failed for {where}. "
                    f"Check SMTP_USERNAME and SMTP_PASSWORD. Detail: {exc}"
                ) from exc
            # Before the timeout check: a connect timeout is both, and nothing was sent.
            if isinstance(exc, aiosmtplib.SMTPConnectError):
                raise SmtpConnectFailedError(
                    f"Could not connect to SMTP server {where}. "
                    f"Check SMTP_HOST, SMTP_PORT, and SMTP_USE_TLS. Detail: {exc}"
                ) from exc
            if isinstance(exc, aiosmtplib.SMTPTimeoutError):
                raise SmtpTimeoutError(
                    f"SMTP server {where} timed out mid-conversation; the message may have been "
                    "sent, so it was not retried. Check before sending again; NW_TIMEOUT may be "
                    f"too low. Detail: {exc}"
                ) from exc
            if isinstance(exc, aiosmtplib.SMTPServerDisconnected):
                raise SmtpDisconnectedError(
                    f"SMTP server {where} closed the connection; the message may have been "
                    f"sent, so it was not retried. Check before sending again. Detail: {exc}"
                ) from exc
            raise

        self._log(
            trace_id,
            "send_email",
            relay,
            "SMTP message sent",
            sender_domain=sender_domain,
            response=str(response),
        )
        return SmtpSendOutput(sent=True, message_id=str(message["Message-ID"]))

    @nw_action("check_connection")
    async def check_connection(
        self, params: CheckConnectionInput, *, trace_id: str
    ) -> CheckConnectionOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "check_connection", relay, "Checking SMTP connection")
        username, password = await self._credentials()
        async with self._session(relay, username, password) as (client, banner):
            await client.noop()
        return CheckConnectionOutput(
            connected=True,
            authenticated=bool(username and password),
            tls=self._tls_mode(relay),  # type: ignore[arg-type]
            banner=banner,
        )

    @nw_action("get_server_capabilities")
    async def get_server_capabilities(
        self, params: GetServerCapabilitiesInput, *, trace_id: str
    ) -> GetServerCapabilitiesOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "get_server_capabilities", relay, "Reading SMTP capabilities")
        # No login: the credentials are not needed to read what the server advertises.
        async with self._session(relay) as (client, banner):
            ehlo = await client.ehlo()  # a plain connection has not sent EHLO yet
        # The first EHLO line is the greeting; each later line is `KEYWORD [parameters]`.
        extensions: Dict[str, str] = {}
        for line in str(ehlo.message).splitlines()[1:]:
            keyword, _, value = line.strip().partition(" ")
            if keyword:
                extensions[keyword.upper()] = value.strip()
        size = extensions.get("SIZE", "")
        return GetServerCapabilitiesOutput(
            banner=banner,
            tls=self._tls_mode(relay),  # type: ignore[arg-type]
            extensions=extensions,
            auth_methods=extensions.get("AUTH", "").upper().split(),
            max_message_size=int(size) if size.isdigit() else None,
        )

    @nw_action("ping")
    async def ping(self, params: PingInput, *, trace_id: str) -> PingOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "ping", relay, "Sending SMTP NOOP")
        async with self._session(relay) as (client, _):
            response = await client.noop()
        return PingOutput(code=response.code, message=str(response.message))

    @nw_action("send_bulk")
    async def send_bulk(self, params: SendBulkInput, *, trace_id: str) -> SendBulkOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(
            trace_id, "send_bulk", relay, "Sending SMTP bulk", message_count=len(params.messages)
        )
        username, password = await self._credentials()
        built = [(_build_message(item), *_dsn_options(item)) for item in params.messages]
        results: List[BulkResult] = []
        async with self._session(relay, username, password) as (client, _):
            for index, (message, mail_options, rcpt_options) in enumerate(built):
                try:
                    await client.send_message(
                        message, mail_options=mail_options, rcpt_options=rcpt_options
                    )
                    results.append(
                        BulkResult(index=index, sent=True, message_id=str(message["Message-ID"]))
                    )
                except (
                    aiosmtplib.SMTPServerDisconnected,
                    aiosmtplib.SMTPTimeoutError,
                    OSError,
                ) as exc:
                    # Simplified: after a lost connection, a timeout or a transport error the session
                    # cannot be trusted (a late reply would be read as the next message's), so the
                    # rest are reported as not sent instead of reconnecting. The message that timed
                    # out may still have been delivered; the caller must not blindly resend it.
                    for rest in range(index, len(built)):
                        results.append(BulkResult(index=rest, sent=False, error=str(exc)[:300]))
                    break
                except aiosmtplib.SMTPException as exc:
                    results.append(BulkResult(index=index, sent=False, error=str(exc)[:300]))
        sent = sum(1 for r in results if r.sent)
        return SendBulkOutput(sent=sent, failed=len(results) - sent, results=results)

    @nw_action("send_raw")
    async def send_raw(self, params: SendRawInput, *, trace_id: str) -> SendRawOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(
            trace_id,
            "send_raw",
            relay,
            "Sending raw SMTP message",
            recipient_count=len(params.recipients),
        )
        username, password = await self._credentials()
        mode = self._tls_mode(relay)
        # The message is validated by SendRawInput (From must match from_email; no Bcc, Received
        # or Return-Path; size cap). The relay's own limits and policy apply on top.
        await aiosmtplib.send(
            params.message,
            sender=str(params.from_email),
            recipients=[str(a) for a in params.recipients],
            hostname=relay.host,
            port=relay.port,
            username=username,
            password=password,
            use_tls=mode == "implicit",
            start_tls=mode == "starttls",
            timeout=self._timeout(),
        )
        message_id = Parser().parsestr(params.message, headersonly=True).get("Message-ID")
        return SendRawOutput(sent=True, message_id=str(message_id) if message_id else None)

    @nw_action("get_tls_info")
    async def get_tls_info(self, params: GetTlsInfoInput, *, trace_id: str) -> GetTlsInfoOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "get_tls_info", relay, "Reading SMTP TLS session")
        mode = self._tls_mode(relay)
        async with self._session(relay) as (client, _):
            ssl_object = client.get_transport_info("ssl_object")
            cert = client.get_transport_info("peercert")
        if ssl_object is None:
            return GetTlsInfoOutput(tls=mode)  # type: ignore[arg-type]
        cipher = ssl_object.cipher()
        certificate = None
        if cert:
            certificate = {
                "subject": _dn(cert.get("subject")),
                "issuer": _dn(cert.get("issuer")),
                "not_before": cert.get("notBefore"),
                "not_after": cert.get("notAfter"),
                "subject_alt_names": [value for _, value in cert.get("subjectAltName", ())],
            }
        return GetTlsInfoOutput(
            tls=mode,  # type: ignore[arg-type]
            version=ssl_object.version(),
            cipher=cipher[0] if cipher else None,
            cipher_bits=cipher[2] if cipher else None,
            certificate=certificate,
        )

    @nw_action("verify_address")
    async def verify_address(
        self, params: VerifyAddressInput, *, trace_id: str
    ) -> VerifyAddressOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "verify_address", relay, "Sending SMTP VRFY")
        username, password = await self._credentials()
        async with self._session(relay, username, password) as (client, _):
            response = await client.vrfy(params.address)
        return VerifyAddressOutput(code=response.code, message=str(response.message))

    @nw_action("expand_list")
    async def expand_list(self, params: ExpandListInput, *, trace_id: str) -> ExpandListOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "expand_list", relay, "Sending SMTP EXPN")
        username, password = await self._credentials()
        async with self._session(relay, username, password) as (client, _):
            response = await client.expn(params.list_name)
        message = str(response.message)
        return ExpandListOutput(code=response.code, message=message, members=message.splitlines())

    @nw_action("get_help")
    async def get_help(self, params: GetHelpInput, *, trace_id: str) -> GetHelpOutput:
        relay = resolve_smtp_relay(self.config)
        self._log(trace_id, "get_help", relay, "Sending SMTP HELP")
        async with self._session(relay) as (client, _):
            text = await client.help()
        return GetHelpOutput(message=str(text))

    @nw_action("validate_address_syntax", requires_auth=False)
    async def validate_address_syntax(
        self, params: ValidateAddressSyntaxInput, *, trace_id: str
    ) -> ValidateAddressSyntaxOutput:
        # Offline: syntax and the RFC 5321 length limits only, no DNS lookup and no server call.
        results: List[AddressCheck] = []
        for address in params.addresses:
            try:
                checked = validate_email(address, check_deliverability=False)
                # RFC 5321 section 4.5.3.1: local part 64, domain 255, path 256 (address 254) octets.
                # email-validator does not enforce the first two.
                local, domain = (len(x.encode()) for x in (checked.local_part, checked.domain))
                if local > 64 or domain > 255 or local + domain + 1 > 254:
                    raise EmailNotValidError("The address is longer than RFC 5321 allows")
                results.append(
                    AddressCheck(address=address, valid=True, normalized=checked.normalized)
                )
            except EmailNotValidError as exc:
                results.append(AddressCheck(address=address, valid=False, reason=str(exc)))
        return ValidateAddressSyntaxOutput(results=results)
