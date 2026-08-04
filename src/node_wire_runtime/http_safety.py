#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""SSRF guards and URL sanitization for outbound REST calls."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from urllib.parse import urlsplit, urlunsplit

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)

_ALLOWED_HOSTS_ENV = "NW_REST_ALLOWED_HOSTS"
_TRUST_ENV_ENV = "NW_REST_TRUST_ENV"


class SsrfBlockedError(ValueError):
    """Raised when an outbound HTTP target resolves to a blocked network destination."""


def load_rest_allowed_hosts() -> frozenset[str]:
    """Return the ``NW_REST_ALLOWED_HOSTS`` egress allowlist (empty = unset)."""
    raw = os.environ.get(_ALLOWED_HOSTS_ENV)
    if not raw or not raw.strip():
        return frozenset()
    return frozenset(
        h.strip().lower().rstrip(".") for h in re.split(r"[\s,]+", raw) if h.strip()
    )


def rest_trust_env() -> bool:
    """Return True when ``NW_REST_TRUST_ENV=true`` (corporate proxy escape hatch)."""
    return os.environ.get(_TRUST_ENV_ENV, "").strip().lower() in {"1", "true", "yes"}


def is_blocked_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True if an address belongs to a blocked range (loopback/private/metadata)."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped
    if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local:
        return True
    if ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
        return True
    if str(ip_obj) in ("169.254.169.254", "fd00:ec2::254"):
        return True
    return False


def _is_blocked_ip_literal(host: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return False
    return is_blocked_ip(ip_obj)


def sanitize_url_for_log(raw_url: str) -> str:
    """Strip query and fragment from URLs before logging (avoid leaking tokens)."""
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except Exception:  # noqa: BLE001
        return "<invalid-url>"


async def assert_safe_destination(url: str) -> None:
    """Resolve the URL host and reject internal/blocked targets before connecting.

    When ``NW_REST_ALLOWED_HOSTS`` is set, the host must additionally appear on
    that egress allowlist.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise SsrfBlockedError("url host is missing")

    if host in _BLOCKED_HOSTNAMES:
        raise SsrfBlockedError("url host is blocked by outbound security policy")
    if _is_blocked_ip_literal(host):
        raise SsrfBlockedError("url host resolves to a blocked network target")

    allowed_hosts = load_rest_allowed_hosts()
    if allowed_hosts and host not in allowed_hosts:
        raise SsrfBlockedError("url host is not on the egress allowlist")

    port = parts.port or (443 if parts.scheme == "https" else 80)

    loop = asyncio.get_event_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SsrfBlockedError(f"url host could not be resolved: {host}") from exc

    if not infos:
        raise SsrfBlockedError(f"url host could not be resolved: {host}")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SsrfBlockedError(f"url host resolved to an unparsable address: {ip_str}")
        if is_blocked_ip(ip_obj):
            raise SsrfBlockedError("url host resolves to a blocked network target")
