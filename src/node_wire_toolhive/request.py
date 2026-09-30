# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""The HTTP request behind the MCP message being handled.

Read from the MCP SDK's per-message ``request_ctx``, not from a contextvar set by HTTP
middleware: with stateful streamable HTTP the session's server task is spawned from the first
request's context, so a middleware contextvar keeps that first request's values for the whole
session (a refreshed token or a later header would never be seen).
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Mapping, Optional

from mcp.server.lowlevel.server import request_ctx

_SESSION_HEADER = "mcp-session-id"
_STDIO_SESSION = "stdio"
PROXY_SECRET_ENV = "NW_PROXY_SECRET"
PROXY_SECRET_HEADER = "x-nw-proxy-secret"
# What nw_stacklok's generated proxy-secret.yaml ships until the operator replaces it.
PROXY_SECRET_PLACEHOLDER = "REPLACE_ME_PROXY_SECRET"

logger = logging.getLogger("node_wire_toolhive")


def request_headers() -> Optional[Mapping[str, str]]:
    """Headers of the HTTP request carrying the current MCP message; ``None`` on stdio."""
    try:
        request = request_ctx.get().request
    except LookupError:
        return None
    headers = getattr(request, "headers", None)
    return headers if headers is not None else None


def bearer_token() -> Optional[str]:
    """The ``Authorization: Bearer`` credential ToolHive forwarded, if any."""
    headers = request_headers()
    if not headers:
        return None
    value = headers.get("authorization") or ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def from_tenant_proxy() -> bool:
    """Whether the request carries the shared secret the ToolHive tenant proxies send.

    Always true when ``NW_PROXY_SECRET`` is unset (local runs). When set, the tenant header is
    only trusted if the request also has a matching ``X-NW-Proxy-Secret``, so a pod that reaches
    the backend directly (no NetworkPolicy enforcement) cannot claim a tenant. The generated
    placeholder is never accepted: anyone reading the manifests knows it.
    """
    expected = os.environ.get(PROXY_SECRET_ENV, "")
    if not expected:
        return True
    if expected == PROXY_SECRET_PLACEHOLDER:
        logger.error(
            "%s is still the generated placeholder; rejecting every tenant request. "
            "Set a random value in proxy-secret.yaml (e.g. `openssl rand -hex 32`).",
            PROXY_SECRET_ENV,
        )
        return False
    headers = request_headers()
    sent = (headers.get(PROXY_SECRET_HEADER) or "") if headers else ""
    return hmac.compare_digest(sent.encode(), expected.encode())


def session_key() -> str:
    """Stable key for the current MCP session (``mcp-session-id``; ``stdio`` without HTTP)."""
    headers = request_headers()
    if headers:
        sid = (headers.get(_SESSION_HEADER) or "").strip()
        if sid:
            return sid
    try:
        return f"session-{id(request_ctx.get().session)}"
    except LookupError:
        return _STDIO_SESSION
