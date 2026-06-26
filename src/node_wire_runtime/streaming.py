#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
import os
import time
import logging
from enum import Enum
from typing import AsyncIterator, Dict, Any, Optional

logger = logging.getLogger("runtime.streaming")


class StreamSignal(str, Enum):
    STARTED = "started"
    CHUNK = "chunk"
    COMPLETED = "completed"
    FAILED = "failed"


def stream_completion_log(trace_id: str, success: bool, *, connector_id: str, action: str) -> None:
    status = StreamSignal.COMPLETED.value if success else StreamSignal.FAILED.value
    msg = "Stream completed" if success else "Stream failed"
    extra = {
        "trace_id": trace_id,
        "connector_id": connector_id,
        "action": action,
        "stream_status": status,
    }
    if success:
        logger.info(
            "%s | trace_id=%s | connector_id=%s | action=%s | status=%s",
            msg,
            trace_id,
            connector_id,
            action,
            status,
            extra=extra,
        )
    else:
        logger.warning(
            "%s | trace_id=%s | connector_id=%s | action=%s | status=%s",
            msg,
            trace_id,
            connector_id,
            action,
            status,
            extra=extra,
        )


def resolve_stream_buffer_ms(override: Optional[int] = None) -> int:
    if override is not None:
        return max(0, min(int(override), 30000))
    val = os.environ.get("NW_STREAM_BUFFER_MS", "0").strip()
    try:
        n = int(val)
    except ValueError:
        n = 0
    return max(0, min(n, 30000))


async def BufferedStreamIterator(
    iterator: AsyncIterator[Dict[str, Any]],
    buffer_ms: int,
    trace_id: str,
    connector_id: str = "agent",
    action: str = "stream",
) -> AsyncIterator[Dict[str, Any]]:
    success = True
    try:
        if buffer_ms <= 0:
            async for item in iterator:
                yield item
            return

        buffer_sec = buffer_ms / 1000.0
        buffer = []
        last_flush = time.monotonic()

        async for item in iterator:
            buffer.append(item)
            now = time.monotonic()
            if now - last_flush >= buffer_sec:
                for b_item in buffer:
                    yield b_item
                buffer.clear()
                last_flush = now

        for b_item in buffer:
            yield b_item
    except Exception:
        success = False
        raise
    finally:
        # Automatically emit completion log when stream ends
        stream_completion_log(trace_id, success, connector_id=connector_id, action=action)
