# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""A minimal MCP streamable-HTTP client for the scripts that check a running server."""

from __future__ import annotations

import json
import time
from typing import Any, Dict

import httpx

ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class McpSession:
    """One MCP session: ``open()`` initializes it, ``rpc()`` sends a request on it."""

    def __init__(self, client: httpx.Client, url: str, headers: Dict[str, str]) -> None:
        self._client, self._url, self._headers = client, url, headers
        self._sid: str | None = None
        self._id = 0

    def _post(self, body: Dict[str, Any]) -> httpx.Response:
        headers = {**ACCEPT, **self._headers}
        if self._sid:
            headers["mcp-session-id"] = self._sid
        return self._client.post(self._url, headers=headers, content=json.dumps(body))

    def rpc(self, method: str, params: Dict[str, Any]) -> Any:
        self._id += 1
        response = self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        )
        response.raise_for_status()
        self._sid = self._sid or response.headers.get("mcp-session-id")
        data = [line for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1][5:] if data else response.text)

    def open(self) -> "McpSession":
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "verify", "version": "1"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self


def wait_for(client: httpx.Client, url: str, timeout: float) -> None:
    """Return once ``url`` accepts connections; raise the last error after ``timeout`` s."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.post(url, headers=ACCEPT, content="{}")
            return
        except httpx.TransportError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
