#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Loopback redirect listener: stray connections must not end the wait for the callback."""

from __future__ import annotations

import asyncio

import pytest

from node_wire_runtime.mcp_client.exceptions import McpOAuthSecurityError
from node_wire_runtime.mcp_client.redirect_listener import LoopbackRedirectListener


async def _send(port: int, raw: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    if raw:
        writer.write(raw)
        await writer.drain()
    if hasattr(writer, "write_eof"):
        writer.write_eof()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data


def _get(path: str) -> bytes:
    return f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stray",
    [
        b"",  # browser pre-connect that sends nothing
        b"garbage\r\n\r\n",  # malformed request line
        b"POST /callback?code=x&state=s HTTP/1.1\r\nHost: h\r\n\r\n",
        b"HEAD /callback HTTP/1.1\r\nHost: h\r\n\r\n",
    ],
)
async def test_stray_connection_does_not_close_listener(stray: bytes) -> None:
    listener = LoopbackRedirectListener()
    binding = await listener.start()
    try:
        await _send(binding.port, stray)
        await _send(binding.port, _get("/callback?code=abc&state=st"))
        callback = await listener.wait_for_callback(timeout=5)
        assert callback.code == "abc"
        assert callback.state == "st"
    finally:
        await listener.close()


@pytest.mark.asyncio
async def test_path_mismatch_still_fails_the_flow() -> None:
    listener = LoopbackRedirectListener()
    binding = await listener.start()
    try:
        await _send(binding.port, _get("/evil?code=abc&state=st"))
        with pytest.raises(McpOAuthSecurityError):
            await listener.wait_for_callback(timeout=5)
    finally:
        await listener.close()


@pytest.mark.asyncio
async def test_start_twice_raises() -> None:
    listener = LoopbackRedirectListener()
    await listener.start()
    try:
        with pytest.raises(RuntimeError):
            await listener.start()
    finally:
        await listener.close()


@pytest.mark.asyncio
async def test_listener_closes_after_callback() -> None:
    listener = LoopbackRedirectListener()
    binding = await listener.start()
    try:
        await _send(binding.port, _get("/callback?code=abc&state=st"))
        await listener.wait_for_callback(timeout=5)
        await asyncio.sleep(0.05)
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", binding.port)
    finally:
        await listener.close()
