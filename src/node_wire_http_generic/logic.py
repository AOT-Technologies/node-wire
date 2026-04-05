from __future__ import annotations

import logging
from typing import Any

import httpx

from node_wire_runtime import BaseConnector, nw_action

from .schema import HttpRequestInput, HttpResponseOutput

logger = logging.getLogger("connectors.http_generic")


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
        logger.info(
            "Preparing HTTP request",
            extra={
                "trace_id": trace_id,
                "connector_id": self.connector_id,
                "action": "request",
                "method": params.method,
                "url": str(params.url),
            },
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=params.method,
                    url=str(params.url),
                    headers=params.headers,
                    params=params.params,
                    json=params.body if isinstance(params.body, (dict, list)) else None,
                    content=None if isinstance(params.body, (dict, list)) else params.body,
                    timeout=30.0,
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
                    "url": str(params.url),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
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
                "url": str(params.url),
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
