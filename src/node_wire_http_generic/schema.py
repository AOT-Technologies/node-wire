#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import ipaddress
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, HttpUrl, field_validator

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata",
}


class HttpRequestInput(BaseModel):
    action: Literal["request"] = "request"
    url: HttpUrl
    method: str
    headers: Optional[Dict[str, str]] = None
    params: Optional[Dict[str, str]] = None
    body: Optional[Any] = None

    @field_validator("method", mode="before")
    @classmethod
    def normalize_and_validate_method(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("method must be a string")
        normalized = value.strip().upper()
        if normalized not in _ALLOWED_METHODS:
            raise ValueError(f"method must be one of: {', '.join(sorted(_ALLOWED_METHODS))}")
        return normalized

    @field_validator("url")
    @classmethod
    def block_internal_targets(cls, value: HttpUrl) -> HttpUrl:
        parts = urlsplit(str(value))
        host = (parts.hostname or "").strip().lower().rstrip(".")
        if host in _BLOCKED_HOSTNAMES:
            raise ValueError("url host is blocked by outbound security policy")
        if _is_blocked_ip_literal(host):
            raise ValueError("url host resolves to a blocked network target")
        return value


def _is_blocked_ip_literal(host: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
        return True
    if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
        return True
    # Explicit cloud metadata target.
    if str(ip_obj) == "169.254.169.254":
        return True
    return False


class HttpResponseOutput(BaseModel):
    status_code: int
    headers: Dict[str, str]
    body: Any
