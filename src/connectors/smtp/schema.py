from __future__ import annotations

import os
import re
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, EmailStr, model_validator


def _strip_env(s: str) -> str:
    return s.strip(" '\"")


def _extract_email(value: str) -> str:
    """Pydantic EmailStr does not accept 'Name <email@addr.com>'."""
    match = re.search(r"<(.+?)>", value)
    return (match.group(1) if match else value).strip()


class SmtpSendInput(BaseModel):
    """
    SMTP send payload. Connection settings default from environment when omitted
    so MCP/REST callers only need to, subject, body.
    """

    action: Literal["send_email"] = "send_email"
    host: str = ""
    port: int = 0
    use_tls: bool = True
    username_secret_key: str = "SMTP_USERNAME"
    password_secret_key: str = "SMTP_PASSWORD"
    from_email: EmailStr
    to: List[EmailStr]
    subject: str
    body: str

    @model_validator(mode="before")
    @classmethod
    def _fill_env_and_normalize(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        if not (values.get("host") or "").strip():
            values["host"] = _strip_env(os.environ.get("SMTP_HOST", "smtp.gmail.com"))
        port_raw = values.get("port")
        if port_raw in (None, "", 0):
            values["port"] = int(_strip_env(os.environ.get("SMTP_PORT", "587")))
        if "use_tls" not in values:
            values["use_tls"] = (
                os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
            )
        if not values.get("username_secret_key"):
            values["username_secret_key"] = "SMTP_USERNAME"
        if not values.get("password_secret_key"):
            values["password_secret_key"] = "SMTP_PASSWORD"

        fe = values.get("from_email")
        if fe is None or not str(fe).strip():
            values["from_email"] = _strip_env(
                os.environ.get("FROM_EMAIL")
                or os.environ.get("SMTP_USERNAME")
                or "noreply@node-wire.local"
            )
        else:
            values["from_email"] = _extract_email(_strip_env(str(fe)))

        # Guardrail: reject placeholder / invalid sender hints from callers
        sender = str(values["from_email"])
        if not sender or "@" not in sender or "system_default" in sender:
            values["from_email"] = _strip_env(
                os.environ.get("FROM_EMAIL")
                or os.environ.get("SMTP_USERNAME")
                or "noreply@node-wire.local"
            )

        raw_to = values.get("to")
        if isinstance(raw_to, str):
            values["to"] = [_extract_email(raw_to)]
        elif isinstance(raw_to, list):
            values["to"] = [_extract_email(str(x)) for x in raw_to]

        return values


class SmtpSendOutput(BaseModel):
    sent: bool
    message_id: Optional[str] = None
