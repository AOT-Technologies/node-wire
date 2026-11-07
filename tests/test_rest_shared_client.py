#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""execute_rest reuses one pooled httpx client per event loop and trust_env value."""

from __future__ import annotations

import asyncio

from node_wire_runtime import rest


def test_shared_client_reused_within_loop() -> None:
    async def _run() -> None:
        a = rest._shared_client(False)
        assert rest._shared_client(False) is a
        assert rest._shared_client(True) is not a
        assert a.follow_redirects is False
        await a.aclose()
        b = rest._shared_client(False)
        assert b is not a and not b.is_closed
        await b.aclose()
        await rest._shared_client(True).aclose()

    asyncio.run(_run())


def test_shared_client_not_shared_across_loops() -> None:
    async def _get():
        return rest._shared_client(False)

    first = asyncio.run(_get())
    second = asyncio.run(_get())
    assert first is not second
