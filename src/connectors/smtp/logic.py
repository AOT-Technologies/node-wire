from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from runtime import BaseConnector, sdk_action
from runtime.mcp_normalizers import normalize_smtp_send_email

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
        logger.info(
            "Preparing SMTP message",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "send_email",
                "host": params.host,
                "port": params.port,
                "from_email": str(params.from_email),
                "recipient_count": len(params.to),
            },
        )

        username = self.secret_provider.get_secret(params.username_secret_key)
        password = self.secret_provider.get_secret(params.password_secret_key)

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
                timeout=30.0,
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
                "response": str(response),
            },
        )

        # aiosmtplib returns (code, message) tuple; message-id is not guaranteed, keep output simple.
        return SmtpSendOutput(sent=True)
