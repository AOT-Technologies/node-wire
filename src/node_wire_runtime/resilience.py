#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import logging
import time
from functools import wraps
from typing import Any, Awaitable, Callable, Coroutine, Mapping, Optional, TypeVar

import httpx
from opentelemetry import metrics
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .errors import ErrorMapper, NestedConnectorActionError, OutcomeUnknownError
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
        logger.warning(
            "Circuit breaker opened",
            extra={
                "breaker": self.name,
                "fail_max": self.fail_max,
                "reset_timeout": self.reset_timeout,
            },
        )

    def allow(self) -> int:
        """Let the call through and return its generation, or raise ``CircuitBreakerError``.

        After ``reset_timeout``, the next caller moves the breaker to half-open
        and becomes the trial; a trial still running after another ``reset_timeout``
        is replaced the same way. The check and the claim do not await, so two
        tasks on this loop cannot both become the trial. Pass the returned
        generation to :meth:`record_success`, :meth:`record_failure` or :meth:`release`.
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

    def release(self, generation: int) -> None:
        """Give up a half-open trial that produced no result, so the next caller can be the trial.

        For a trial that was cancelled (e.g. a client disconnect): it says nothing about the
        upstream, but must not keep the breaker half-open for another ``reset_timeout``. The breaker
        goes back to open with the timer already expired. Does nothing in any other state, or for
        a call that is not the live trial (it started before the last change).
        """
        if generation != self._generation or self._state_name != "half-open":
            return
        self._state_name = "open"
        self._opened_at = time.monotonic() - self.reset_timeout

    def record_success(self, generation: int) -> None:
        # A call that started before the last trip cannot close the breaker or
        # stand in for the half-open trial.
        if generation != self._generation:
            return
        self._fail_counter = 0
        self._state_name = "closed"

    def record_failure(self, generation: int) -> None:
        """Count one failed invocation; open the breaker at ``fail_max``.

        Never raises: the call that opens the breaker reports its own error. Only the next call
        sees ``CircuitBreakerError``, from :meth:`allow`.
        """
        # A call that started before the last trip must not restart the timeout.
        if generation != self._generation:
            return
        if self._state_name == "half-open":
            self._trip()
            return
        self._fail_counter += 1
        if self._fail_counter < self.fail_max:
            return
        self._trip()


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


# Methods whose repeat has the same effect as one call (RFC 9110 9.2.2).
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})
# A request carrying one of these is safe to resend: the upstream deduplicates it
# (``Idempotency-Key``) or creates only if absent (FHIR conditional create, ``If-None-Exist``).
IDEMPOTENCY_HEADERS = ("idempotency-key", "if-none-exist")
# The request never left: retrying cannot apply it twice.
_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


def request_is_idempotent(method: str, headers: Optional[Mapping[str, str]] = None) -> bool:
    """Whether resending this HTTP request cannot apply its effect twice."""
    if method.upper() in IDEMPOTENT_METHODS:
        return True
    names = {name.lower() for name in (headers or {})}
    return any(header in names for header in IDEMPOTENCY_HEADERS)


def _http_request(exc: BaseException) -> Optional[httpx.Request]:
    if not isinstance(exc, (httpx.RequestError, httpx.HTTPStatusError)):
        return None
    try:
        return exc.request
    except RuntimeError:  # an httpx error raised without its request
        return None


def _retry_is_safe(exc: BaseException, idempotent: Optional[bool]) -> bool:
    """Whether retrying after ``exc`` cannot apply a write twice.

    A request that was never sent, and a 429, are always safe. Otherwise the action's declared
    ``idempotent`` decides; an action that declares nothing is judged by the failed HTTP request
    (its method and idempotency headers). A failure with no HTTP request behind it, in an action
    that declares nothing, keeps the old behaviour and is retried.
    """
    if isinstance(exc, _NOT_SENT_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    if idempotent is not None:
        return idempotent
    request = _http_request(exc)
    if request is None:
        return True
    return request_is_idempotent(request.method, request.headers)


def _outcome_unknown(exc: Exception, action: str) -> OutcomeUnknownError:
    request = _http_request(exc)
    what = f"{request.method} request" if request is not None else "call"
    return OutcomeUnknownError(
        f"The {what} for action {action or '(unknown)'!r} failed after it may have reached the "
        f"upstream ({type(exc).__name__}: {exc}). It was not retried because it is not "
        "idempotent; check whether it took effect before trying again."
    )


# Transport-level failures: the upstream or the network to it is unhealthy. A connector may map
# these to FATAL (e.g. http_generic maps ``httpx.RequestError``), so the category alone is not enough.
_TRANSPORT_ERRORS = (OSError, httpx.TransportError)


def _counts_against_breaker(exc: Exception, category: ErrorCategory) -> bool:
    """Whether a failed invocation says the upstream is unhealthy.

    RETRYABLE failures and transport-level FATAL ones do. BUSINESS and AUTH mean the upstream
    answered; any other FATAL is a bug or input no connector mapped. None of those may trip the
    breaker, or one caller's bad input would take the connector down for everyone sharing it.
    """
    if isinstance(exc, NestedConnectorActionError):
        return exc.counts_against_breaker
    if isinstance(exc, OutcomeUnknownError):
        return True
    if category is ErrorCategory.RETRYABLE:
        return True
    return category is ErrorCategory.FATAL and isinstance(exc, _TRANSPORT_ERRORS)


def with_resilience(
    breaker: CircuitBreaker | Callable[[], CircuitBreaker] | None,
    max_attempts: int = 3,
    base_wait: float = 0.5,
    max_wait: float = 5.0,
    connector_id: str = "",
    action: str = "",
    idempotent: Optional[bool] = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Coroutine[Any, Any, T]]]:
    """
    Decorator that applies retry (Tenacity) and a circuit breaker
    around an async function that may raise exceptions.

    The breaker sees one invocation, not each retry: it is asked once up front, and told once
    at the end whether the upstream looked healthy (see :func:`_counts_against_breaker`).
    ``breaker=None`` retries without a breaker (a nested call whose outer call owns it).

    A RETRYABLE failure is retried only when that cannot apply a write twice (see
    :func:`_retry_is_safe`; ``idempotent`` is the action's own declaration, ``None`` if it
    makes none). Otherwise it is raised as :class:`OutcomeUnknownError` (``OUTCOME_UNKNOWN``).
    A :class:`NestedConnectorActionError` is never retried: the nested call already retried.

    ``connector_id`` and ``action`` are low-cardinality labels attached to the
    retry and circuit-breaker metrics; they carry no request-specific identity.
    """
    _metric_attrs = {"connector.id": connector_id, "connector.action": action}

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        async def _with_retries(trace_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type(Exception),
                    stop=stop_after_attempt(max_attempts),
                    wait=wait_exponential(multiplier=base_wait, max=max_wait),
                    reraise=True,
                ):
                    with attempt:
                        try:
                            return await fn(*args, **kwargs)
                        except Exception as exc:  # noqa: BLE001
                            mapped = ErrorMapper.resolve(exc, connector_id=connector_id)
                            nested = isinstance(exc, NestedConnectorActionError)
                            if (
                                mapped.category is ErrorCategory.RETRYABLE
                                and not nested
                                and not _retry_is_safe(exc, idempotent)
                            ):
                                logger.warning(
                                    "Retryable error on a non-idempotent call; not retrying",
                                    extra={
                                        "trace_id": trace_id,
                                        "error_code": mapped.code,
                                        "error_type": type(exc).__name__,
                                        "error_message": str(exc),
                                    },
                                )
                                unknown = _outcome_unknown(exc, action)
                                unknown.__cause__ = exc
                                raise _AbortRetry(unknown)
                            if mapped.category is not ErrorCategory.RETRYABLE or nested:
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

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            trace_id: str = kwargs.get("trace_id", "unknown-trace")
            if breaker is None:
                return await _with_retries(trace_id, args, kwargs)
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
                _circuit_breaker_rejections.add(1, attributes=_metric_attrs)
                raise

            # ``Exception``, not ``BaseException``: a cancelled call (e.g. a client disconnect)
            # says nothing about the upstream, so it neither counts nor closes the breaker.
            try:
                result = await _with_retries(trace_id, args, kwargs)
            except asyncio.CancelledError:
                # Simplified: only cancellation; KeyboardInterrupt and SystemExit end the process.
                # If this was the half-open trial, hand the slot to the next caller instead of
                # leaving everyone rejected for another reset_timeout.
                current_breaker.release(generation)
                raise
            except Exception as exc:  # noqa: BLE001
                category = ErrorMapper.resolve(exc, connector_id=connector_id).category
                if _counts_against_breaker(exc, category):
                    current_breaker.record_failure(generation)
                elif category in (ErrorCategory.BUSINESS, ErrorCategory.AUTH):
                    # The upstream answered, so this resets the consecutive-failure count and
                    # resolves a half-open trial (the same as pybreaker's ``exclude=`` did).
                    current_breaker.record_success(generation)
                else:
                    # Any other FATAL (a bug, unmapped input) says nothing about the upstream:
                    # it neither counts nor closes the breaker. A half-open trial gives up its
                    # slot, like a cancelled one.
                    current_breaker.release(generation)
                raise
            current_breaker.record_success(generation)
            return result

        return wrapper

    return decorator
