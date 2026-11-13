#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""REST admin routes wait for tenant_persistence._lock in a thread, not on the event loop.

save_tenants holds the lock while it writes the secret store and tenants file, so
taking it on the loop would stall every other request for the write's duration.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from starlette.requests import Request

import bindings.rest_api.app as app_mod
from node_wire_runtime import tenant_persistence


def test_secrets_list_keys_does_not_block_loop_while_lock_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_mod, "_config_tenant", lambda request: "t1")
    request = Request({"type": "http", "headers": [], "query_string": b""})
    locked, release = threading.Event(), threading.Event()

    def _hold_lock() -> None:
        with tenant_persistence._lock:
            locked.set()
            release.wait(timeout=2.0)  # bounded, so a regression fails instead of hanging

    holder = threading.Thread(target=_hold_lock, daemon=True)
    holder.start()
    assert locked.wait(timeout=2.0)

    async def _scenario() -> tuple[bool, float]:
        start = time.monotonic()
        task = asyncio.create_task(app_mod.secrets_list_keys("cid", request, config_name="c"))
        await asyncio.sleep(0.05)
        loop_ran_while_waiting = not task.done()
        release.set()
        response = await task
        assert response.status_code == 200
        return loop_ran_while_waiting, time.monotonic() - start

    try:
        loop_ran_while_waiting, elapsed = asyncio.run(_scenario())
    finally:
        release.set()
        holder.join(timeout=3.0)

    assert loop_ran_while_waiting
    assert elapsed < 1.0
