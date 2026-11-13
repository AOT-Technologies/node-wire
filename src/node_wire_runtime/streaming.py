#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
import asyncio
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
    CANCELLED = "cancelled"


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


def _stream_cancelled_log(trace_id: str, *, connector_id: str, action: str) -> None:
    status = StreamSignal.CANCELLED.value
    logger.info(
        "Stream cancelled | trace_id=%s | connector_id=%s | action=%s | status=%s",
        trace_id,
        connector_id,
        action,
        status,
        extra={
            "trace_id": trace_id,
            "connector_id": connector_id,
            "action": action,
            "stream_status": status,
        },
    )


async def BufferedStreamIterator(
    iterator: AsyncIterator[Dict[str, Any]],
    buffer_ms: int,
    trace_id: str,
    connector_id: str = "agent",
    action: str = "stream",
) -> AsyncIterator[Dict[str, Any]]:
    """Yield ``iterator``'s items, batched into flushes at most every ``buffer_ms``.

    Items already received are yielded before an upstream error is re-raised. Logs one line
    when the stream ends: completed, failed, or cancelled (the task was cancelled or the
    consumer stopped early).
    """
    outcome: Optional[StreamSignal] = None
    buffer: list[Dict[str, Any]] = []
    try:
        if buffer_ms <= 0:
            async for item in iterator:
                yield item
        else:
            buffer_sec = buffer_ms / 1000.0
            last_flush = time.monotonic()

            async for item in iterator:
                buffer.append(item)
                now = time.monotonic()
                if now - last_flush >= buffer_sec:
                    pending, buffer = buffer, []
                    for b_item in pending:
                        yield b_item
                    last_flush = now

            pending, buffer = buffer, []
            for b_item in pending:
                yield b_item
        outcome = StreamSignal.COMPLETED
    except Exception:
        outcome = StreamSignal.FAILED
        # What the upstream sent before failing still reaches the consumer.
        pending, buffer = buffer, []
        for b_item in pending:
            yield b_item
        raise
    except (asyncio.CancelledError, GeneratorExit):
        outcome = StreamSignal.CANCELLED
        raise
    finally:
        if outcome is StreamSignal.CANCELLED or outcome is None:
            _stream_cancelled_log(trace_id, connector_id=connector_id, action=action)
        else:
            stream_completion_log(
                trace_id,
                outcome is StreamSignal.COMPLETED,
                connector_id=connector_id,
                action=action,
            )
