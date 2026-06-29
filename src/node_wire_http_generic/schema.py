#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import ipaddress
import os
import re
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, HttpUrl, field_validator

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
    "metadata",
}

# Optional egress allowlist (preferred control). Comma/space separated hostnames.
_ALLOWED_HOSTS_ENV = "NW_HTTP_GENERIC_ALLOWED_HOSTS"


def load_allowed_hosts() -> frozenset[str]:
    """Return the configured egress allowlist of permitted destination hosts.

    Empty (unset) means "no allowlist configured" — the connector then falls
    back to the denylist + resolved-IP range checks. When set, only the listed
    hostnames may be reached.
    """
    raw = os.environ.get(_ALLOWED_HOSTS_ENV)
    if not raw or not raw.strip():
        return frozenset()
    return frozenset(h.strip().lower().rstrip(".") for h in re.split(r"[\s,]+", raw) if h.strip())


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


def is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if an already-parsed address belongs to a blocked range.

    Shared by the schema-time literal check and the connection-time resolved-IP
    check in :mod:`node_wire_http_generic.logic`, so both apply identical policy.
    """
    # Normalize IPv4-mapped IPv6 (``::ffff:127.0.0.1``) to its IPv4 form so the
    # range checks below catch loopback/private targets smuggled through IPv6.
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
        return True
    if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
        return True
    # Explicit cloud metadata target (IMDS).
    if str(ip_obj) in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return False


def _is_blocked_ip_literal(host: str) -> bool:
    # Reject non-dotted-decimal numeric hosts (decimal ``2130706433``, octal
    # ``0177.0.0.1``) that ``ipaddress`` rejects but the OS socket layer accepts.
    # We only treat a host as an IP literal when it parses in canonical form.
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return False
    return is_blocked_ip(ip_obj)


class HttpResponseOutput(BaseModel):
    status_code: int
    headers: Dict[str, str]
    body: Any
