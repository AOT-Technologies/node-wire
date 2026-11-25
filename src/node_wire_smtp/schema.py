#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import base64
import binascii
import os
import re
from email import policy
from email.parser import Parser
from email.utils import getaddresses
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

_FORBIDDEN_RELAY_KEYS = frozenset({"host", "port", "use_tls"})
_HEADER_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f]")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")  # RFC 5322 field names, no separators
_CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$")
_MAX_RAW_MESSAGE = 10_000_000
_MAX_ATTACHMENT_BASE64 = 14_000_000  # about 10 MB of file
_MAX_BULK_ATTACHMENTS_BASE64 = 40_000_000  # all attachments of one send_bulk: about 30 MB of files
_RAW_FORBIDDEN_HEADERS = ("Bcc", "Received", "Return-Path")
# Set by the connector itself, so a caller header may not replace them.
_RESERVED_HEADERS = frozenset(
    {
        "from",
        "to",
        "cc",
        "bcc",
        "subject",
        "reply-to",
        "sender",
        "date",
        "message-id",
        "mime-version",
        "return-path",
        "received",
        "content-type",
        "content-transfer-encoding",
        "content-disposition",
        "importance",
        "x-priority",
    }
)


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


def _default_sender() -> Optional[str]:
    """FROM_EMAIL, else SMTP_USERNAME, else None (then from_email is required)."""
    return _strip_env(os.environ.get("FROM_EMAIL") or os.environ.get("SMTP_USERNAME") or "") or None


def _as_address_list(raw: Any) -> Any:
    if isinstance(raw, str):
        return [_extract_email(raw)]
    if isinstance(raw, list):
        return [_extract_email(str(x)) for x in raw]
    return raw


class SmtpAttachment(BaseModel):
    """One attachment. The content is base64 text, so it travels in JSON."""

    filename: str
    content_base64: str = Field(max_length=_MAX_ATTACHMENT_BASE64)
    content_type: str = "application/octet-stream"

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        _reject_unsafe_header_value(value, "filename")
        if not value or "/" in value or "\\" in value:
            raise ValueError("filename must be a plain file name")
        return value

    @field_validator("content_base64")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 must be valid base64") from exc
        return value

    @field_validator("content_type")
    @classmethod
    def _validate_content_type(cls, value: str) -> str:
        if not _CONTENT_TYPE_RE.match(value):
            raise ValueError("content_type must look like type/subtype")
        return value


class SmtpSendInput(BaseModel):
    """
    Send an email via SMTP.

    Only ``to``, ``subject``, and ``body`` are required. SMTP connection settings
    (``SMTP_HOST``, ``SMTP_PORT``, ``SMTP_USE_TLS``, or ``host`` / ``port`` / ``use_tls`` in
    connectors.yaml) are configured server-side only; they cannot be supplied in the request.

    Credentials (username and password) are **not** part of this schema.
    They are managed entirely by the :class:`AuthProvider` injected into the
    connector by the factory, keeping secrets out of the request payload.
    """

    action: Literal["send_email"] = "send_email"
    from_email: Optional[EmailStr] = None
    to: Union[str, List[EmailStr]]
    subject: str
    body: str
    cc: Optional[List[EmailStr]] = None
    bcc: Optional[List[EmailStr]] = None
    reply_to: Optional[EmailStr] = None
    html_body: Optional[str] = None
    attachments: Optional[List[SmtpAttachment]] = None
    headers: Optional[Dict[str, str]] = None
    priority: Optional[Literal["high", "normal", "low"]] = None
    dsn_notify: Optional[List[Literal["SUCCESS", "FAILURE", "DELAY", "NEVER"]]] = None
    dsn_return: Optional[Literal["FULL", "HDRS"]] = None

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        return _reject_unsafe_header_value(value, "subject")

    @field_validator("headers")
    @classmethod
    def _validate_headers(cls, value: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        for name, val in (value or {}).items():
            if not _HEADER_NAME_RE.match(name):
                raise ValueError(f"header name {name!r} is not a valid field name")
            if name.lower() in _RESERVED_HEADERS:
                raise ValueError(f"header {name!r} is set by the connector and cannot be replaced")
            _reject_unsafe_header_value(val, f"header {name}")
        return value

    @field_validator("dsn_notify")
    @classmethod
    def _validate_dsn_notify(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value and "NEVER" in value and len(value) > 1:
            raise ValueError("dsn_notify NEVER cannot be combined with other values")
        return value

    @model_validator(mode="after")
    def _require_sender_and_recipient(self) -> "SmtpSendInput":
        if not self.from_email:
            raise ValueError("from_email is required: pass it, or set FROM_EMAIL on the server")
        if not (self.to or self.cc or self.bcc):
            raise ValueError("at least one recipient is required in to, cc or bcc")
        return self

    @model_validator(mode="before")
    @classmethod
    def _reject_relay_fields_and_normalize(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        values = dict(values)  # do not change the caller's dict

        for key in _FORBIDDEN_RELAY_KEYS:
            values.pop(key, None)

        if "from" in values and not values.get("from_email"):
            values["from_email"] = values.pop("from")

        fe = values.get("from_email")
        if fe is None or not str(fe).strip():
            values["from_email"] = _default_sender()
        else:
            values["from_email"] = _extract_email(_strip_env(str(fe)))

        sender = str(values["from_email"] or "")
        if sender and ("@" not in sender or "system_default" in sender):
            values["from_email"] = _default_sender()

        for field in ("to", "cc", "bcc"):
            if field in values:
                values[field] = _as_address_list(values[field])
        if isinstance(values.get("reply_to"), str):
            values["reply_to"] = _extract_email(values["reply_to"])

        return values


class SmtpSendOutput(BaseModel):
    sent: bool
    message_id: Optional[str] = None


class _Strict(BaseModel):
    """Input of an action other than send_email. Extra fields (a host, a port) are an error."""

    model_config = ConfigDict(extra="forbid")


def _plain_text(value: str, field_name: str) -> str:
    """A one-line argument for a server command: no control characters, at most 255 characters."""
    _reject_unsafe_header_value(value, field_name)
    if not value.strip() or len(value) > 255:
        raise ValueError(f"{field_name} must be 1 to 255 characters")
    return value


class CheckConnectionInput(_Strict):
    """Connect to the configured relay, log in with the server-side credentials, and NOOP."""

    action: Literal["check_connection"] = "check_connection"


class CheckConnectionOutput(BaseModel):
    connected: bool
    authenticated: bool
    tls: Literal["implicit", "starttls", "none"]
    banner: str


class GetServerCapabilitiesInput(_Strict):
    """EHLO to the configured relay (no login) and report what it advertises."""

    action: Literal["get_server_capabilities"] = "get_server_capabilities"


class GetServerCapabilitiesOutput(BaseModel):
    banner: str
    tls: Literal["implicit", "starttls", "none"]
    extensions: Dict[str, str]
    auth_methods: List[str]
    max_message_size: Optional[int] = None


class PingInput(_Strict):
    """Connect to the configured relay (no login), send NOOP, and quit."""

    action: Literal["ping"] = "ping"


class PingOutput(BaseModel):
    code: int
    message: str


class SendBulkInput(_Strict):
    """Send up to 100 messages over one connection. Each message has the fields of a single send."""

    action: Literal["send_bulk"] = "send_bulk"
    # Simplified: at most 100 messages per call (the size of an SMTP server's recipient buffer).
    messages: List[SmtpSendInput] = Field(min_length=1, max_length=100)

    # Simplified: only the batch total is capped (not the attachments per message). Every message is
    # built before the first is sent, so this bounds what one request can hold in memory.
    @model_validator(mode="after")
    def _bounded_batch(self) -> "SendBulkInput":
        total = sum(len(a.content_base64) for m in self.messages for a in m.attachments or [])
        if total > _MAX_BULK_ATTACHMENTS_BASE64:
            raise ValueError(
                f"the attachments of one send_bulk may total {_MAX_BULK_ATTACHMENTS_BASE64} base64 "
                f"characters (about 30 MB of files); send fewer or smaller messages per call"
            )
        return self


class BulkResult(BaseModel):
    index: int
    sent: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class SendBulkOutput(BaseModel):
    sent: int
    failed: int
    results: List[BulkResult]


class SendRawInput(_Strict):
    """Send a finished RFC 5322 message (headers, blank line, body) to the given envelope recipients."""

    action: Literal["send_raw"] = "send_raw"
    from_email: EmailStr
    recipients: List[EmailStr] = Field(min_length=1, max_length=100)
    # Simplified: a flat 10 MB cap on the text; the relay's own SIZE limit applies as well.
    message: str = Field(min_length=1, max_length=_MAX_RAW_MESSAGE)

    @field_validator("from_email", mode="before")
    @classmethod
    def _extract_sender(cls, value: Any) -> Any:
        return _extract_email(value) if isinstance(value, str) else value

    @field_validator("recipients", mode="before")
    @classmethod
    def _extract_recipients(cls, value: Any) -> Any:
        return _as_address_list(value)

    @model_validator(mode="after")
    def _check_headers(self) -> "SendRawInput":
        """The header From must be the envelope sender, and the headers a relay adds or hides
        (Bcc, Received, Return-Path) may not be set by the caller."""
        headers = Parser(policy=policy.default).parsestr(self.message, headersonly=True)
        for name in _RAW_FORBIDDEN_HEADERS:
            if headers.get_all(name):
                raise ValueError(f"the message may not contain a {name} header")
        senders = getaddresses([str(v) for v in headers.get_all("From") or []])
        if len(senders) != 1 or senders[0][1].lower() != str(self.from_email).lower():
            raise ValueError("the message needs exactly one From header, equal to from_email")
        return self


class SendRawOutput(BaseModel):
    sent: bool
    message_id: Optional[str] = None


class GetTlsInfoInput(_Strict):
    """Connect to the configured relay (no login) and report the TLS session."""

    action: Literal["get_tls_info"] = "get_tls_info"


class GetTlsInfoOutput(BaseModel):
    tls: Literal["implicit", "starttls", "none"]
    version: Optional[str] = None
    cipher: Optional[str] = None
    cipher_bits: Optional[int] = None
    certificate: Optional[Dict[str, Any]] = None


class VerifyAddressInput(_Strict):
    """Ask the relay to VRFY an address. Many servers disable it (RFC 5321 section 3.5.2)."""

    action: Literal["verify_address"] = "verify_address"
    address: str

    @field_validator("address")
    @classmethod
    def _check(cls, value: str) -> str:
        return _plain_text(value, "address")


class VerifyAddressOutput(BaseModel):
    code: int
    message: str


class ExpandListInput(_Strict):
    """Ask the relay to EXPN a mailing list. Many servers disable it (RFC 5321 section 3.5.2)."""

    action: Literal["expand_list"] = "expand_list"
    list_name: str

    @field_validator("list_name")
    @classmethod
    def _check(cls, value: str) -> str:
        return _plain_text(value, "list_name")


class ExpandListOutput(BaseModel):
    code: int
    message: str
    members: List[str]


class GetHelpInput(_Strict):
    """Send HELP to the configured relay (no login)."""

    action: Literal["get_help"] = "get_help"


class GetHelpOutput(BaseModel):
    message: str


class ValidateAddressSyntaxInput(_Strict):
    """Check addresses offline (RFC 5321 syntax and length limits). Nothing is sent to any server."""

    action: Literal["validate_address_syntax"] = "validate_address_syntax"
    addresses: List[str] = Field(min_length=1, max_length=100)


class AddressCheck(BaseModel):
    address: str
    valid: bool
    normalized: Optional[str] = None
    reason: Optional[str] = None


class ValidateAddressSyntaxOutput(BaseModel):
    results: List[AddressCheck]
