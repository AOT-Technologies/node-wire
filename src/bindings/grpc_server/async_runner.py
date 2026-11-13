#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


def _call_timeout_seconds() -> float:
    """``NW_GRPC_CALL_TIMEOUT_SECONDS`` (default 300)."""
    try:
        value = float(os.environ.get("NW_GRPC_CALL_TIMEOUT_SECONDS", "300"))
    except ValueError:
        return 300.0
    return value if value > 0 else 300.0


class BackgroundAsyncRunner:
    """Run coroutines on one persistent event loop from sync worker threads."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="grpc-async-loop",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        if self._loop is None:
            raise RuntimeError("Background async runner is not started")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        # Bounded wait: a hung connector would otherwise hold one of the server's
        # worker threads forever. Well above NW_TIMEOUT x retries by default.
        timeout = _call_timeout_seconds()
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"gRPC call did not finish within {timeout:g}s") from None
