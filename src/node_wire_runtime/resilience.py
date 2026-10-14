#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from opentelemetry import metrics
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .errors import ErrorMapper
from .models import ErrorCategory

logger = logging.getLogger("runtime.resilience")

_meter = metrics.get_meter("runtime")
_retry_counter = _meter.create_counter(
    "connector.retries",
    unit="1",
    description="Retryable errors encountered during execution (triggers a retry unless attempts are exhausted)",
)
_circuit_breaker_rejections = _meter.create_counter(
    "connector.circuit_breaker_rejections",
    unit="1",
    description="Calls rejected because the tenant circuit breaker was open",
)

T = TypeVar("T")


class CircuitBreakerError(Exception):
    """Raised when a call is rejected because the breaker is open."""


class _BreakerState:
    def __init__(self, name: str) -> None:
        self.name = name


class CircuitBreaker:
    """Async circuit breaker. One instance per tenant on each connector.

    Simplified: one successful trial closes the breaker. No listeners and no
    excluded-exception list; this runtime did not use those options.
    """

    def __init__(
        self,
        fail_max: int = 5,
        reset_timeout: float = 60,
        name: str | None = None,
    ) -> None:
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self.name = name
        self._state_name = "closed"
        self._fail_counter = 0
        self._opened_at = 0.0
        # Bumped on every trip and every move to half-open. A result carrying an
        # older generation is from a call that started before that change.
        self._generation = 0

    @property
    def fail_counter(self) -> int:
        return self._fail_counter

    @property
    def state(self) -> _BreakerState:
        return _BreakerState(self._state_name)

    def open(self) -> None:
        self._trip()

    def _trip(self) -> None:
        self._state_name = "open"
        self._opened_at = time.monotonic()
        self._generation += 1

    def allow(self) -> int:
        """Let the call through and return its generation, or raise ``CircuitBreakerError``.

        After ``reset_timeout``, the next caller moves the breaker to half-open
        and becomes the trial; a trial still running after another ``reset_timeout``
        is replaced the same way. The check and the claim do not await, so two
        tasks on this loop cannot both become the trial. Pass the returned
        generation to :meth:`record_success` / :meth:`record_failure`.
        """
        if self._state_name == "closed":
            return self._generation
        # Simplified: a trial that outlives reset_timeout is abandoned, like an open period.
        if time.monotonic() < self._opened_at + self.reset_timeout:
            raise CircuitBreakerError("Circuit breaker is open")
        self._state_name = "half-open"
        self._opened_at = time.monotonic()
        self._generation += 1
        return self._generation

    def record_success(self, generation: int) -> None:
        # A call that started before the last trip cannot close the breaker or
        # stand in for the half-open trial.
        if generation != self._generation:
            return
        self._fail_counter = 0
        self._state_name = "closed"

    def record_failure(self, exc: BaseException, generation: int) -> None:
        # A call that started before the last trip must not restart the timeout
        # or hide its own exception behind CircuitBreakerError.
        if generation != self._generation:
            return
        if self._state_name == "half-open":
            self._trip()
            return
        self._fail_counter += 1
        if self._fail_counter < self.fail_max:
            return
        self._trip()
        if isinstance(exc, Exception):
            raise CircuitBreakerError("Failures threshold reached, circuit breaker opened")


class _AbortRetry(BaseException):
    """Wraps a non-retryable exception to escape tenacity's retry loop."""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


def _resolve_breaker(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker],
) -> CircuitBreaker:
    if isinstance(breaker, CircuitBreaker):
        return breaker
    return breaker() if callable(breaker) else breaker


async def _run_through_breaker(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker],
    fn: Callable[..., Awaitable[T]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    trace_id: str,
) -> T:
    """Run one async call through the breaker, awaiting the result."""
    current_breaker = _resolve_breaker(breaker)
    try:
        generation = current_breaker.allow()
    except CircuitBreakerError:
        logger.error(
            "Circuit breaker is OPEN; rejecting call",
            extra={
                "trace_id": trace_id,
                "component": "resilience",
                "error": "circuit open",
            },
        )
        raise

    try:
        result = await fn(*args, **kwargs)
    except BaseException as exc:
        current_breaker.record_failure(exc, generation)
        raise
    current_breaker.record_success(generation)
    return result


def with_resilience(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker],
    max_attempts: int = 3,
    base_wait: float = 0.5,
    max_wait: float = 5.0,
    connector_id: str = "",
    action: str = "",
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator that applies retry (Tenacity) and a circuit breaker
    around an async function that may raise exceptions.

    ``connector_id`` and ``action`` are low-cardinality labels attached to the
    retry and circuit-breaker metrics; they carry no request-specific identity.
    """
    _metric_attrs = {"connector.id": connector_id, "connector.action": action}

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            trace_id: str = kwargs.get("trace_id", "unknown-trace")

            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(Exception),
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=base_wait, max=max_wait),
                    reraise=True,
                ):
                    with attempt:
                        try:
                            return await _run_through_breaker(breaker, fn, args, kwargs, trace_id)
                        except Exception as exc:  # noqa: BLE001
                            mapped = ErrorMapper.resolve(exc, connector_id=connector_id)
                            if isinstance(exc, CircuitBreakerError):
                                _circuit_breaker_rejections.add(1, attributes=_metric_attrs)
                            if mapped.category is not ErrorCategory.RETRYABLE:
                                # Non-retryable: escape the retry loop entirely. The failure is
                                # logged once, at error level, by BaseConnector.run (with its
                                # invocation_failure audit line); this is only the retry decision.
                                logger.debug(
                                    "Non-retryable error; not retrying",
                                    extra={
                                        "trace_id": trace_id,
                                        "error_code": mapped.code,
                                        "error_category": mapped.category.value,
                                        "error_type": type(exc).__name__,
                                        "error_message": str(exc),
                                    },
                                )
                                raise _AbortRetry(exc)

                            _retry_counter.add(
                                1,
                                attributes={**_metric_attrs, "error_code": mapped.code},
                            )
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
