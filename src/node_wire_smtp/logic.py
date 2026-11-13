#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from typing import ClassVar, Dict, Tuple, Type

import aiosmtplib

from node_wire_runtime import BaseConnector, ErrorCategory, sdk_action

from .normalizers import normalize_smtp_send_email
from .relay import SmtpRelayNotAllowedError, resolve_smtp_relay
from .schema import SmtpSendInput, SmtpSendOutput

logger = logging.getLogger("connectors.smtp")


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
    """
    SMTP connector for sending emails via aiosmtplib.
    """

    connector_id = "smtp"
    output_model = SmtpSendOutput

    # Scoped to smtp only.
    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        SmtpRelayNotAllowedError: (ErrorCategory.FATAL, "SMTP_RELAY_NOT_ALLOWED"),
        SmtpAuthFailedError: (ErrorCategory.AUTH, "SMTP_AUTH_ERROR"),
        SmtpConnectFailedError: (ErrorCategory.RETRYABLE, "SMTP_CONNECT_ERROR"),
        SmtpTimeoutError: (ErrorCategory.FATAL, "SMTP_TIMEOUT"),
        SmtpDisconnectedError: (ErrorCategory.FATAL, "SMTP_DISCONNECTED"),
        aiosmtplib.errors.SMTPConnectError: (ErrorCategory.RETRYABLE, "SMTP_CONNECT_ERROR"),
        aiosmtplib.errors.SMTPTimeoutError: (ErrorCategory.FATAL, "SMTP_TIMEOUT"),
        aiosmtplib.errors.SMTPAuthenticationError: (ErrorCategory.AUTH, "SMTP_AUTH_ERROR"),
        aiosmtplib.errors.SMTPException: (ErrorCategory.BUSINESS, "SMTP_ERROR"),
    }

    @sdk_action(
        "send_email",
        alias_tolerant=True,
        mcp_normalize=normalize_smtp_send_email,
    )
    async def send_email(self, params: SmtpSendInput, *, trace_id: str) -> SmtpSendOutput:
        relay = resolve_smtp_relay()

        _sender_domain = (
            str(params.from_email).split("@")[-1] if "@" in str(params.from_email) else "unknown"
        )
        logger.info(
            "Preparing SMTP message",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_email",
                "host": relay.host,
                "port": relay.port,
                "sender_domain": _sender_domain,
                "recipient_count": len(params.to),
            },
        )

        creds = await self._auth_provider.get_client_credentials()
        if creds is not None and isinstance(creds, (list, tuple)) and len(creds) == 2:
            username, password = str(creds[0]), str(creds[1])
        else:
            try:
                username = self.secret_provider.get_secret("SMTP_USERNAME")
                password = self.secret_provider.get_secret("SMTP_PASSWORD")
            except Exception:
                username = os.environ.get("SMTP_USERNAME", "")
                password = os.environ.get("SMTP_PASSWORD", "")

        message = EmailMessage()
        message["From"] = str(params.from_email)
        message["To"] = ", ".join(str(addr) for addr in params.to)
        message["Subject"] = params.subject
        message.set_content(params.body)

        use_implicit = relay.port == 465
        try:
            response = await aiosmtplib.send(
                message,
                hostname=relay.host,
                port=relay.port,
                username=username,
                password=password,
                use_tls=use_implicit,
                start_tls=relay.use_tls and not use_implicit,
                timeout=float(os.getenv("NW_TIMEOUT", "30.0")),
            )
        except SmtpRelayNotAllowedError:
            raise
        except Exception as exc:  # noqa: BLE001
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

        logger.info(
            "SMTP message sent",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_email",
                "host": relay.host,
                "port": relay.port,
                "sender_domain": _sender_domain,
                "response": str(response),
            },
        )

        return SmtpSendOutput(sent=True)
