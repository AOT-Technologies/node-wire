"""
FastMCP Server Entrypoint — SMTP
================================
Standalone MCP server exposing the SMTP email tool:
  • smtp_send_email

Usage:
    python -m agents.smtp_mcp

Environment variables:
    SMTP_HOST (default: smtp.gmail.com)
    SMTP_PORT (default: 587)
    SMTP_USE_TLS (default: true)
    SMTP_USERNAME
    SMTP_PASSWORD
    FROM_EMAIL (optional; fallback sender address)
"""
from __future__ import annotations

import logging
import os
import re
import uuid

from dotenv import load_dotenv

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agents.smtp_mcp")


def _extract_email(value: str) -> str:
    # Pydantic EmailStr does not like "Name <email@addr.com>"
    match = re.search(r"<(.+?)>", value)
    return (match.group(1) if match else value).strip()


def _make_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError("mcp SDK not installed. Run: pip install 'node-wire[agents]'") from exc

    from bindings.factory import ConnectorFactory
    from connectors import auto_register
    from connectors.smtp.schema import SmtpSendInput

    auto_register()
    factory = ConnectorFactory()
    factory.load()

    mcp = FastMCP("nw-smtp")

    @mcp.tool(
        name="smtp_send_email",
        description=(
            "Send an email to a recipient via SMTP. "
            "Credentials are picked up from environment variables. "
            "You can specify multiple recipients mapped to a single comma separated string."
        ),
    )
    async def smtp_send_email(
        to_email: str,
        subject: str,
        body: str,
        from_email: str = "",
    ) -> dict:
        trace_id = str(uuid.uuid4())
        smtp = factory._connectors.get("smtp")
        if not smtp:
            raise RuntimeError("smtp connector not configured")

        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip(" '\"")
        smtp_port_raw = os.environ.get("SMTP_PORT", "587").strip(" '\"")
        smtp_port = int(smtp_port_raw)
        smtp_use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"

        sender = from_email.strip(" '\"")
        if not sender or "@" not in sender or "system_default" in sender:
            sender = (
                os.environ.get("FROM_EMAIL")
                or os.environ.get("SMTP_USERNAME")
                or "noreply@node-wire.local"
            ).strip(" '\"")

        sender = _extract_email(sender)
        recipients = [_extract_email(addr.strip()) for addr in to_email.split(",") if addr.strip()]

        logger.info("SMTP Tool | from=%s to=%s subject=%s", sender, recipients, subject)

        params = SmtpSendInput(
            host=smtp_host,
            port=smtp_port,
            use_tls=smtp_use_tls,
            username_secret_key="SMTP_USERNAME",
            password_secret_key="SMTP_PASSWORD",
            from_email=sender,
            to=recipients,
            subject=subject,
            body=body,
        )
        result = await smtp.internal_execute(params, trace_id=trace_id)
        return {"sent": result.sent, "message_id": getattr(result, "message_id", None)}

    logger.info("Registered 1 SMTP MCP tools")
    return mcp


def main() -> None:
    server = _make_server()
    logger.info("Starting nw-smtp MCP server (stdio transport)")
    server.run()


if __name__ == "__main__":
    main()
