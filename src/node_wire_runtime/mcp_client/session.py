#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Streamable-HTTP session handshake state shared by the HTTP MCP clients."""

from __future__ import annotations

import asyncio
import enum
from typing import Awaitable, Callable, Optional

import httpx

Handshake = Callable[[], Awaitable[None]]
Send = Callable[[], Awaitable[httpx.Response]]


class McpSessionClosedError(RuntimeError):
    """The MCP client was closed with ``aclose()``; create a new client."""


class SessionState(str, enum.Enum):
    NEW = "new"
    INITIALIZING = "initializing"
    READY = "ready"
    CLOSED = "closed"


class McpHttpSession:
    """
    One MCP streamable-HTTP session: ``initialize`` once, then send requests.

    Concurrent first calls share a single handshake. A request that gets HTTP 404
    for the session id it carried means the server dropped the session; the
    session is reset, re-initialized and the request retried once. After
    :meth:`close` every call raises :class:`McpSessionClosedError`.
    """

    def __init__(self) -> None:
        self.state = SessionState.NEW
        self.session_id: Optional[str] = None
        self._init_lock = asyncio.Lock()

    def _check_open(self) -> None:
        if self.state is SessionState.CLOSED:
            raise McpSessionClosedError("MCP client is closed")

    async def ensure_ready(self, handshake: Handshake) -> None:
        """Run *handshake* unless the session is already initialized.

        *handshake* sends ``initialize`` / ``notifications/initialized`` and stores
        the server's ``Mcp-Session-Id`` in :attr:`session_id`.
        """
        self._check_open()
        if self.state is SessionState.READY:
            return
        async with self._init_lock:
            self._check_open()
            if self.state is SessionState.READY:
                return
            self.state = SessionState.INITIALIZING
            self.session_id = None
            try:
                await handshake()
            except BaseException:
                if self.state is SessionState.INITIALIZING:
                    self.state = SessionState.NEW
                    self.session_id = None
                raise
            if self.state is SessionState.INITIALIZING:
                self.state = SessionState.READY

    async def send(self, request: Send, handshake: Handshake) -> httpx.Response:
        """Send one request inside the session, re-initializing once on HTTP 404."""
        await self.ensure_ready(handshake)
        sent_with = self.session_id
        resp = await request()
        if resp.status_code == 404 and sent_with is not None:
            self._expire(sent_with)
            await self.ensure_ready(handshake)
            resp = await request()
        return resp

    def _expire(self, session_id: str) -> None:
        # Only the first caller to see the 404 resets; others already re-initialized.
        if self.state is SessionState.READY and self.session_id == session_id:
            self.state = SessionState.NEW
            self.session_id = None

    def close(self) -> None:
        self.state = SessionState.CLOSED
        self.session_id = None
