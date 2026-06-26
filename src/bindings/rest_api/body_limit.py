#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable

_LIMIT_METHODS = frozenset({"POST", "PUT", "PATCH"})
_LIMIT_PATH_PREFIXES = ("/connectors/", "/scenarios/")


class BodyTooLarge(Exception):
    """Raised when an incoming request body exceeds the configured limit."""


def _path_requires_limit(scope: dict[str, Any]) -> bool:
    if scope.get("type") != "http":
        return False
    if scope.get("method", "GET") not in _LIMIT_METHODS:
        return False
    path = scope.get("path", "")
    return path.startswith(_LIMIT_PATH_PREFIXES)


def _header_value(headers: list[tuple[bytes, bytes]], name: str) -> bytes | None:
    name_lower = name.lower().encode("ascii")
    for key, value in headers:
        if key.lower() == name_lower:
            return value
    return None


async def _send_payload_too_large(send: Callable[..., Awaitable[None]]) -> None:
    body = json.dumps({"detail": "Request body too large"}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class MaxBodySizeMiddleware:
    """Reject oversized request bodies before route handlers parse JSON."""

    def __init__(self, app: Any, *, max_body_bytes: int = 10_485_760) -> None:
        self.app = app
        self._default_max_body_bytes = max_body_bytes

    def _max_bytes(self) -> int:
        return int(os.environ.get("NW_REST_MAX_BODY_BYTES", str(self._default_max_body_bytes)))

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if not _path_requires_limit(scope):
            await self.app(scope, receive, send)
            return

        max_bytes = self._max_bytes()
        headers = list(scope.get("headers") or [])
        content_length = _header_value(headers, "content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    await _send_payload_too_large(send)
                    return
            except ValueError:
                pass

        bytes_read = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal bytes_read
            message = await receive()
            if message["type"] != "http.request":
                return message

            bytes_read += len(message.get("body", b""))
            if bytes_read > max_bytes:
                while message.get("more_body", False):
                    message = await receive()
                raise BodyTooLarge()

            return message

        try:
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            await _send_payload_too_large(send)
