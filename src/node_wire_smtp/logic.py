from __future__ import annotations

import logging
import os
from email.message import EmailMessage

import aiosmtplib

from node_wire_runtime import BaseConnector, sdk_action
from node_wire_runtime.mcp_normalizers import normalize_smtp_send_email

from .schema import SmtpSendInput, SmtpSendOutput

logger = logging.getLogger("connectors.smtp")


class SmtpConnector(BaseConnector):
    """
    SMTP connector for sending emails via aiosmtplib.
    """

    connector_id = "smtp"
    output_model = SmtpSendOutput

    @sdk_action(
        "send_email",
        alias_tolerant=True,
        mcp_normalize=normalize_smtp_send_email,
    )
    async def send_email(self, params: SmtpSendInput, *, trace_id: str) -> SmtpSendOutput:
        # Derive a domain-only hint so the sender identity (PII) is never written to logs.
        _sender_domain = (
            str(params.from_email).split("@")[-1] if "@" in str(params.from_email) else "unknown"
        )
        logger.info(
            "Preparing SMTP message",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_email",
                "host": params.host,
                "port": params.port,
                "sender_domain": _sender_domain,
                "recipient_count": len(params.to),
            },
        )

        # Resolve credentials from AuthProvider (injected by factory).
        # Falls back to environment variables for backward compatibility when
        # the connector is instantiated without an explicit auth_provider.
        creds = await self._auth_provider.get_client_credentials()
        if creds is not None and isinstance(creds, (list, tuple)) and len(creds) == 2:
            username, password = str(creds[0]), str(creds[1])
        else:
            # Fallback: resolve from environment / secret_provider directly.
            try:
                username = self.secret_provider.get_secret("SMTP_USERNAME")
                password = self.secret_provider.get_secret("SMTP_PASSWORD")
            except Exception:
                import os as _os

                username = _os.environ.get("SMTP_USERNAME", "")
                password = _os.environ.get("SMTP_PASSWORD", "")

        message = EmailMessage()
        message["From"] = str(params.from_email)
        message["To"] = ", ".join(str(addr) for addr in params.to)
        message["Subject"] = params.subject
        message.set_content(params.body)

        use_implicit = params.port == 465
        try:
            response = await aiosmtplib.send(
                message,
                hostname=params.host,
                port=params.port,
                username=username,
                password=password,
                use_tls=use_implicit,
                start_tls=params.use_tls and not use_implicit,
                timeout=float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SMTP send failed",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "send_email",
                    "host": params.host,
                    "port": params.port,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        logger.info(
            "SMTP message sent",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_email",
                "host": params.host,
                "port": params.port,
                "sender_domain": _sender_domain,
                "response": str(response),
            },
        )

        # aiosmtplib returns (code, message) tuple; message-id is not guaranteed, keep output simple.
        return SmtpSendOutput(sent=True)
