#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Outbound egress policy for the generic HTTP connector (SSRF mitigation)."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)

_NON_DOTTED_DECIMAL_HOST = re.compile(r"^\d+$")
_OCTAL_IPV4_PART = re.compile(r"^0[0-7]+$")


class HttpEgressBlockedError(ValueError):
    """Raised when a URL target is blocked by outbound security policy."""


@dataclass(frozen=True)
class ValidatedEgress:
    original_url: str
    hostname: str
    port: int
    explicit_port: int | None
    scheme: str
    path: str
    query: str
    pinned_ips: tuple[IPAddress, ...]


def normalize_ip(addr: IPAddress | str) -> IPAddress:
    ip_obj = ipaddress.ip_address(addr) if isinstance(addr, str) else addr
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        return ip_obj.ipv4_mapped
    return ip_obj


def is_blocked_address(ip: IPAddress | str) -> bool:
    ip_obj = normalize_ip(ip)
    if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
        return True
    if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
        return True
    if str(ip_obj) == "169.254.169.254":
        return True
    return False


def _normalize_hostname(host: str) -> str:
    return host.strip().lower().rstrip(".")


def _reject_non_dotted_decimal_host(host: str) -> None:
    if _NON_DOTTED_DECIMAL_HOST.match(host):
        raise HttpEgressBlockedError("url host uses a blocked numeric encoding")
    if "." in host and not host.startswith("["):
        for part in host.split("."):
            if _OCTAL_IPV4_PART.match(part):
                raise HttpEgressBlockedError("url host uses a blocked numeric encoding")


def validate_host_literal(host: str) -> None:
    """Sync validation of hostname / IP literal (no DNS)."""
    normalized = _normalize_hostname(host)
    if not normalized:
        raise HttpEgressBlockedError("url host is blocked by outbound security policy")

    if normalized in _BLOCKED_HOSTNAMES:
        raise HttpEgressBlockedError("url host is blocked by outbound security policy")

    _reject_non_dotted_decimal_host(normalized)

    literal = normalized.strip("[]")
    try:
        ip_obj = normalize_ip(ipaddress.ip_address(literal))
    except ValueError:
        return

    if is_blocked_address(ip_obj):
        raise HttpEgressBlockedError("url host is a blocked network target")


def load_egress_allowlist() -> frozenset[str] | None:
    raw = os.environ.get("NW_HTTP_GENERIC_EGRESS_ALLOWLIST")
    if raw is None or not raw.strip():
        return None
    hosts = {_normalize_hostname(part) for part in raw.split(",") if part.strip()}
    return frozenset(hosts) if hosts else None


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _format_ip_for_url(ip: IPAddress) -> str:
    if isinstance(ip, ipaddress.IPv6Address):
        return f"[{ip}]"
    return str(ip)


def _host_header(hostname: str, explicit_port: int | None, scheme: str) -> str:
    if explicit_port is not None and explicit_port != _default_port(scheme):
        return f"{hostname}:{explicit_port}"
    return hostname


def build_pinned_url(validated: ValidatedEgress, pinned_ip: IPAddress) -> str:
    ip_host = _format_ip_for_url(pinned_ip)
    if validated.explicit_port is not None:
        netloc = f"{ip_host}:{validated.explicit_port}"
    else:
        netloc = ip_host
    return urlunsplit(
        (validated.scheme, netloc, validated.path, validated.query, ""),
    )


def build_pinned_request_kwargs(
    validated: ValidatedEgress,
    *,
    base_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build httpx request kwargs using the first validated pinned IP."""
    pinned_ip = validated.pinned_ips[0]
    headers = dict(base_headers or {})
    headers["Host"] = _host_header(validated.hostname, validated.explicit_port, validated.scheme)

    kwargs: dict[str, Any] = {
        "url": build_pinned_url(validated, pinned_ip),
        "headers": headers,
        "follow_redirects": False,
    }
    if validated.scheme in ("https", "wss"):
        kwargs["extensions"] = {"sni_hostname": validated.hostname}
    return kwargs


async def resolve_host(host: str, port: int) -> tuple[IPAddress, ...]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HttpEgressBlockedError("could not resolve url host") from exc

    pinned: list[IPAddress] = []
    seen: set[IPAddress] = set()
    for _family, _type, _proto, _canonname, sockaddr in infos:
        ip_obj = normalize_ip(ipaddress.ip_address(sockaddr[0]))
        if ip_obj in seen:
            continue
        seen.add(ip_obj)
        if is_blocked_address(ip_obj):
            raise HttpEgressBlockedError("url host resolves to a blocked network target")
        pinned.append(ip_obj)

    if not pinned:
        raise HttpEgressBlockedError("could not resolve url host")
    return tuple(pinned)


async def validate_egress_url(url: str) -> ValidatedEgress:
    parts = urlsplit(url)
    hostname = _normalize_hostname(parts.hostname or "")
    validate_host_literal(hostname)

    allowlist = load_egress_allowlist()
    if allowlist is not None and hostname not in allowlist:
        raise HttpEgressBlockedError("url host is not on the egress allowlist")

    scheme = parts.scheme or "http"
    explicit_port = parts.port
    port = explicit_port if explicit_port is not None else _default_port(scheme)

    literal = hostname.strip("[]")
    try:
        pinned_ips: tuple[IPAddress, ...] = (normalize_ip(ipaddress.ip_address(literal)),)
        if is_blocked_address(pinned_ips[0]):
            raise HttpEgressBlockedError("url host is a blocked network target")
    except ValueError:
        pinned_ips = await resolve_host(hostname, port)

    return ValidatedEgress(
        original_url=url,
        hostname=hostname,
        port=port,
        explicit_port=explicit_port,
        scheme=scheme,
        path=parts.path or "",
        query=parts.query or "",
        pinned_ips=pinned_ips,
    )


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport marker; pinning is applied via URL + Host + SNI extensions."""

    def __init__(self, *, validated: ValidatedEgress, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.validated = validated
