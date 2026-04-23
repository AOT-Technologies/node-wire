from __future__ import annotations

import os
import re
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, EmailStr, model_validator


def _strip_env(s: str) -> str:
    return s.strip(" '\"")


def _extract_email(value: str) -> str:
    """Pydantic EmailStr does not accept 'Name <email@addr.com>'."""
    match = re.search(r"<(.+?)>", value)
    return (match.group(1) if match else value).strip()


class SmtpSendInput(BaseModel):
    """
    Send an email via SMTP.

    Only ``to``, ``subject``, and ``body`` are required — connection settings
    (``host``, ``port``, ``use_tls``) fall back to server-side environment
    variables when not supplied.

    Credentials (username and password) are **not** part of this schema.
    They are managed entirely by the :class:`AuthProvider` injected into the
    connector by the factory, keeping secrets out of the request payload.
    """

    action: Literal["send_email"] = "send_email"
    host: str = ""
    port: int = 0
    use_tls: bool = True
    from_email: Optional[EmailStr] = None
    to: Union[str, List[EmailStr]]
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

        if "from" in values and not values.get("from_email"):
            values["from_email"] = values.pop("from")

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
