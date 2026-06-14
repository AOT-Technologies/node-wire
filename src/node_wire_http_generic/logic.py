#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from node_wire_runtime import BaseConnector, nw_action

from .egress import (
    HttpEgressBlockedError,
    PinnedAsyncHTTPTransport,
    build_pinned_request_kwargs,
    validate_egress_url,
)
from .schema import HttpRequestInput, HttpResponseOutput

logger = logging.getLogger("connectors.http_generic")


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

        try:
            validated = await validate_egress_url(str(params.url))
            pinned_kwargs = build_pinned_request_kwargs(
                validated,
                base_headers=params.headers,
            )
            timeout = float(os.getenv("AOT_CONNECTOR_TIMEOUT", "30.0"))
            transport = PinnedAsyncHTTPTransport(validated=validated, trust_env=False)
            async with httpx.AsyncClient(
                timeout=timeout, trust_env=False, transport=transport
            ) as client:
                response = await client.request(
                    method=params.method,
                    params=params.params,
                    json=params.body if isinstance(params.body, (dict, list)) else None,
                    content=None if isinstance(params.body, (dict, list)) else params.body,
                    timeout=timeout,
                    **pinned_kwargs,
                )
        except HttpEgressBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001
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

        headers: dict[str, Any] = {k: v for k, v in response.headers.items()}

        return HttpResponseOutput(
            status_code=response.status_code,
            headers=headers,
            body=response.text,
        )
