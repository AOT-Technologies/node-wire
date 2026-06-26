#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Loopback redirect URI listener for OAuth authorization code callback."""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from .exceptions import McpOAuthFlowAborted, McpOAuthSecurityError

logger = logging.getLogger("runtime.mcp_client.redirect_listener")

_HTML_SUCCESS = (
    b"<html><body><h1>Authorization complete</h1>"
    b"<p>You can close this window and return to node-wire.</p></body></html>"
)
_HTML_ERROR = (
    b"<html><body><h1>Authorization failed</h1>"
    b"<p>Return to node-wire and try again.</p></body></html>"
)


@dataclass(frozen=True)
class AuthorizationCallback:
    """Query parameters from the redirect URI callback."""

    code: Optional[str]
    state: Optional[str]
    error: Optional[str]
    error_description: Optional[str]


@dataclass(frozen=True)
class LoopbackRedirectBinding:
    """Ephemeral loopback redirect URI bound to a single callback."""

    redirect_uri: str
    host: str
    port: int
    path: str


def _normalize_path(path: str) -> str:
    p = path.strip() or "/callback"
    return p if p.startswith("/") else f"/{p}"


class LoopbackRedirectListener:
    """
    Bind ``127.0.0.1`` on an ephemeral port and accept exactly one HTTP callback.

    Per MCP security requirements: exact path match, single callback, then close.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        path: str = "/callback",
    ) -> None:
        self._host = host
        self._path = _normalize_path(path)
        self._server: Optional[asyncio.AbstractServer] = None
        self._binding: Optional[LoopbackRedirectBinding] = None
        self._callback_future: Optional[asyncio.Future[AuthorizationCallback]] = None

    @property
    def binding(self) -> LoopbackRedirectBinding:
        if self._binding is None:
            raise RuntimeError("Listener not started; call start() first")
        return self._binding

    async def start(self) -> LoopbackRedirectBinding:
        loop = asyncio.get_running_loop()
        self._callback_future = loop.create_future()

        # Bind explicit loopback address on an ephemeral port.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._host, 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        self._binding = LoopbackRedirectBinding(
            redirect_uri=f"http://{self._host}:{port}{self._path}",
            host=self._host,
            port=port,
            path=self._path,
        )

        async def _handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                request_line = await reader.readline()
                if not request_line:
                    return
                parts = request_line.decode("latin-1", errors="replace").split()
                if len(parts) < 2:
                    return
                method, target = parts[0], parts[1]
                # Consume headers
                while True:
                    line = await reader.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break

                parsed = urlsplit(target)
                if method.upper() != "GET":
                    writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                    await writer.drain()
                    return

                if parsed.path != self._path:
                    writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
                    await writer.drain()
                    if self._callback_future and not self._callback_future.done():
                        self._callback_future.set_exception(
                            McpOAuthSecurityError(
                                f"Redirect path mismatch: expected {self._path!r}, "
                                f"got {parsed.path!r}"
                            )
                        )
                    return

                qs = parse_qs(parsed.query)
                callback = AuthorizationCallback(
                    code=_first(qs, "code"),
                    state=_first(qs, "state"),
                    error=_first(qs, "error"),
                    error_description=_first(qs, "error_description"),
                )
                if callback.error:
                    body = _HTML_ERROR
                    if self._callback_future and not self._callback_future.done():
                        self._callback_future.set_exception(
                            McpOAuthFlowAborted(callback.error_description or callback.error)
                        )
                else:
                    body = _HTML_SUCCESS
                    if self._callback_future and not self._callback_future.done():
                        self._callback_future.set_result(callback)

                writer.write(b"HTTP/1.1 200 OK\r\n")
                writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
                writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode())
                writer.write(body)
                await writer.drain()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                if self._server:
                    self._server.close()

        self._server = await asyncio.start_server(_handle, sock=sock)
        logger.debug(
            "Loopback redirect listener started",
            extra={"redirect_uri": self._binding.redirect_uri},
        )
        return self._binding

    async def wait_for_callback(self, timeout: float = 300.0) -> AuthorizationCallback:
        if self._callback_future is None:
            raise RuntimeError("Listener not started")
        try:
            return await asyncio.wait_for(asyncio.shield(self._callback_future), timeout)
        except asyncio.TimeoutError as exc:
            raise McpOAuthFlowAborted("Authorization timed out waiting for redirect") from exc

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


def _first(qs: dict, key: str) -> Optional[str]:
    vals = qs.get(key)
    if vals:
        return vals[0]
    return None
