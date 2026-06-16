#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from pybreaker import CircuitBreaker, CircuitBreakerError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .errors import ErrorMapper
from .models import ErrorCategory

logger = logging.getLogger("runtime.resilience")

T = TypeVar("T")


class _AbortRetry(BaseException):
    """Wraps a non-retryable exception to escape tenacity's retry loop."""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


def _resolve_breaker(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker],
) -> CircuitBreaker:
    # CircuitBreaker instances are callable (__call__); treat concrete instances
    # before callable check so we don't invoke them like factory functions.
    if isinstance(breaker, CircuitBreaker):
        return breaker
    return breaker() if callable(breaker) else breaker


def with_resilience(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker],
    max_attempts: int = 3,
    base_wait: float = 0.5,
    max_wait: float = 5.0,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator that applies retry (Tenacity) and circuit breaking (PyBreaker)
    around an async function that may raise exceptions.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            trace_id: str = kwargs.get("trace_id", "unknown-trace")

            async def _call() -> T:
                current_breaker = _resolve_breaker(breaker)
                breaker_state = current_breaker.state

                if breaker_state.name == "open":
                    logger.error(
                        "Circuit breaker is OPEN; rejecting call",
                        extra={
                            "trace_id": trace_id,
                            "component": "resilience",
                            "error": "circuit open",
                        },
                    )
                    raise CircuitBreakerError("Circuit breaker is open")

                breaker_state.before_call(fn, *args, **kwargs)
                for listener in current_breaker.listeners:
                    listener.before_call(current_breaker, fn, *args, **kwargs)

                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    breaker_state._handle_error(exc)
                else:
                    breaker_state._handle_success()
                    return result

            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(Exception),
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=base_wait, max=max_wait),
                    reraise=True,
                ):
                    with attempt:
                        try:
                            return await _call()
                        except Exception as exc:  # noqa: BLE001
                            mapped = ErrorMapper.resolve(exc)
                            if mapped.category is not ErrorCategory.RETRYABLE:
                                # Non-retryable: log, then escape the retry loop entirely.
                                logger.error(
                                    "Non-retryable error during execution",
                                    extra={
                                        "trace_id": trace_id,
                                        "error_code": mapped.code,
                                        "error_category": mapped.category.value,
                                        "error_type": type(exc).__name__,
                                        "error_message": str(exc),
                                    },
                                )
                                raise _AbortRetry(exc)

                            logger.warning(
                                "Retryable error during execution; will retry",
                                extra={
                                    "trace_id": trace_id,
                                    "error_code": mapped.code,
                                    "error_category": mapped.category.value,
                                    "attempt_number": attempt.retry_state.attempt_number,
                                    "error_type": type(exc).__name__,
                                    "error_message": str(exc),
                                },
                            )
                            raise
            except _AbortRetry as abort:
                raise abort.cause

            # Should not be reached because reraise=True ensures RetryError is propagated.
            raise RuntimeError("Exhausted retries without success")

        return wrapper

    return decorator
