#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import os
import re
from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, EmailStr, field_validator, model_validator

_FORBIDDEN_RELAY_KEYS = frozenset({"host", "port", "use_tls"})
_HEADER_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")


def _reject_unsafe_header_value(value: str, field_name: str) -> str:
    if _HEADER_UNSAFE_RE.search(value):
        raise ValueError(f"{field_name} must not contain control characters or newlines")
    return value


def _strip_env(s: str) -> str:
    return s.strip(" '\"")


def _extract_email(value: str) -> str:
    """Pydantic EmailStr does not accept 'Name <email@addr.com>'."""
    match = re.search(r"<(.+?)>", value)
    return (match.group(1) if match else value).strip()


class SmtpSendInput(BaseModel):
    """
    Send an email via SMTP.

    Only ``to``, ``subject``, and ``body`` are required. SMTP connection settings
    (``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USE_TLS``) are configured server-side
    only — they cannot be supplied in the request payload.

    Credentials (username and password) are **not** part of this schema.
    They are managed entirely by the :class:`AuthProvider` injected into the
    connector by the factory, keeping secrets out of the request payload.
    """

    action: Literal["send_email"] = "send_email"
    from_email: Optional[EmailStr] = None
    to: Union[str, List[EmailStr]]
    subject: str
    body: str

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        return _reject_unsafe_header_value(value, "subject")

    @model_validator(mode="before")
    @classmethod
    def _reject_relay_fields_and_normalize(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values

        for key in _FORBIDDEN_RELAY_KEYS:
            if key in values:
                values.pop(key, None)

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
