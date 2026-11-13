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
import time
from collections import OrderedDict
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
_DNS_CACHE_ENV = "NW_REST_DNS_CACHE_SECONDS"
_DNS_CACHE_DEFAULT_SECONDS = 30.0
_DNS_CACHE_MAX_ENTRIES = 1024

# (host, port) -> monotonic expiry of a passed SSRF DNS check. Only passes are kept:
# blocked or unresolvable hosts are resolved again on every call.
_dns_checked_ok: "OrderedDict[tuple[str, int], float]" = OrderedDict()


class SsrfBlockedError(ValueError):
    """Raised when an outbound HTTP target resolves to a blocked network destination."""


def load_rest_allowed_hosts() -> frozenset[str]:
    """Return the ``NW_REST_ALLOWED_HOSTS`` egress allowlist (empty = unset)."""
    raw = os.environ.get(_ALLOWED_HOSTS_ENV)
    if not raw or not raw.strip():
        return frozenset()
    return frozenset(h.strip().lower().rstrip(".") for h in re.split(r"[\s,]+", raw) if h.strip())


def _url_host(url: str) -> str:
    return (urlsplit(url).hostname or "").strip().lower().rstrip(".")


def assert_base_url_allowed(url: str, *, baked_base_url: str = "") -> None:
    """Reject a runtime ``base_url`` whose host is outside the connector allowlist.

    The host of ``baked_base_url`` (the spec server) is always allowed. Any other
    host requires ``NW_REST_ALLOWED_HOSTS`` to be set and to contain it. The scheme
    must be ``https``, or ``http`` when the spec server itself is ``http``.
    """
    # Simplified: refuse "@" and "\" anywhere instead of reconciling URL parsers.
    if "@" in url or "\\" in url:
        raise ValueError("base_url host is not allowed")
    if urlsplit(url).scheme.lower() not in ("https", urlsplit(baked_base_url).scheme.lower()):
        raise ValueError("base_url host is not allowed")
    host = _url_host(url)
    if not host:
        raise ValueError("base_url host is not allowed")
    baked_host = _url_host(baked_base_url) if baked_base_url else ""
    if baked_host and host == baked_host:
        return
    allowed = load_rest_allowed_hosts()
    if not allowed or host not in allowed:
        raise ValueError("base_url host is not allowed")


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

    # Simplified: a passed check is reused for a short TTL instead of resolving on every
    # call (in Kubernetes, ndots:5 turns one lookup into several DNS queries). This barely
    # widens the DNS-rebinding window: httpx resolves separately, and pooled connections
    # keep their original IP anyway. NW_REST_DNS_CACHE_SECONDS=0 turns it off.
    ttl = _dns_cache_seconds()
    key = (host, port)
    if ttl > 0:
        expiry = _dns_checked_ok.get(key)
        if expiry is not None and expiry > time.monotonic():
            return

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

    if ttl > 0:
        _dns_checked_ok[key] = time.monotonic() + ttl
        _dns_checked_ok.move_to_end(key)
        while len(_dns_checked_ok) > _DNS_CACHE_MAX_ENTRIES:
            _dns_checked_ok.popitem(last=False)


def _dns_cache_seconds() -> float:
    raw = os.environ.get(_DNS_CACHE_ENV, "").strip()
    if not raw:
        return _DNS_CACHE_DEFAULT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DNS_CACHE_DEFAULT_SECONDS


def clear_dns_check_cache() -> None:
    """Forget every passed SSRF DNS check (tests, or after a DNS change)."""
    _dns_checked_ok.clear()
