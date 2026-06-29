#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from node_wire_runtime import BaseConnector, nw_action

from .schema import (
    HttpRequestInput,
    HttpResponseOutput,
    is_blocked_ip,
    load_allowed_hosts,
)

logger = logging.getLogger("connectors.http_generic")


class SsrfBlockedError(ValueError):
    """Raised when an outbound HTTP target resolves to a blocked network destination."""


async def _assert_safe_destination(url: str) -> None:
    """Resolve the URL host and reject internal/blocked targets before connecting.

    Closes the gap between schema-time literal validation and the address the OS
    actually dials: a hostname (or an alternate IP encoding) that resolves to
    loopback, RFC1918, link-local or the cloud metadata service is rejected here,
    immediately before the request is issued (mitigating DNS-name SSRF). When
    ``NW_HTTP_GENERIC_ALLOWED_HOSTS`` is set, the host must additionally appear on
    that egress allowlist.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise SsrfBlockedError("url host is missing")

    allowed_hosts = load_allowed_hosts()
    if allowed_hosts and host not in allowed_hosts:
        raise SsrfBlockedError("url host is not on the egress allowlist")

    port = parts.port or (443 if parts.scheme == "https" else 80)

    # Resolve via the event loop's resolver (non-blocking) and validate every
    # address the host maps to — a single hostname can return multiple records.
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


def _sanitize_url_for_log(raw_url: str) -> str:
    """
    Remove query and fragment from URLs before logging to avoid leaking tokens/PII.
    """
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


class HttpGenericConnector(BaseConnector):
    """
    Lightweight HTTP connector for generic REST integrations.
    """

    connector_id = "http_generic"
    output_model = HttpResponseOutput

    @nw_action("request")
    async def request(self, params: HttpRequestInput, *, trace_id: str) -> HttpResponseOutput:
        """
        Perform an HTTP request using httpx.

        All potential network errors are raised and mapped by the runtime's
        ErrorMapper, with detailed, human-readable logs at the connector level.
        """
        safe_url = _sanitize_url_for_log(str(params.url))
        logger.info(
            "Preparing HTTP request",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "request",
                "method": params.method,
                "url": safe_url,
            },
        )

        # Resolve-and-validate immediately before connecting. This is the
        # authoritative SSRF gate; schema validation only sees the literal host.
        await _assert_safe_destination(str(params.url))

        try:
            timeout = float(os.getenv("NW_TIMEOUT", "30.0"))
            async with httpx.AsyncClient(
                timeout=timeout, trust_env=False, follow_redirects=False
            ) as client:
                response = await client.request(
                    method=params.method,
                    url=str(params.url),
                    headers=params.headers,
                    params=params.params,
                    json=params.body if isinstance(params.body, (dict, list)) else None,
                    content=None if isinstance(params.body, (dict, list)) else params.body,
                    timeout=timeout,
                )
        except Exception as exc:  # noqa: BLE001
            # Let ErrorMapper classify the exception, but log clear context here.
            logger.error(
                "HTTP request failed before receiving a response",
                extra={
                    "trace_id": trace_id,
                    "connector_id": self.connector_id,
                    "action": "request",
                    "method": params.method,
                    "url": safe_url,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        logger.info(
            "HTTP request completed",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "request",
                "method": params.method,
                "url": safe_url,
                "status_code": response.status_code,
            },
        )

        # Do not log full body to avoid leaking sensitive data.
        headers: dict[str, Any] = {k: v for k, v in response.headers.items()}

        return HttpResponseOutput(
            status_code=response.status_code,
            headers=headers,
            body=response.text,
        )
