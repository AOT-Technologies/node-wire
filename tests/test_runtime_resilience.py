#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import contextlib
import time
from functools import partial

import httpx
import pytest
from pydantic import BaseModel
from node_wire_runtime import BaseConnector, ErrorCategory, ErrorMapper, nw_action
from node_wire_runtime.resilience import (
    CircuitBreaker,
    CircuitBreakerError,
    _resolve_breaker,
    with_resilience,
)


class RetryableTestError(Exception):
    pass


class FatalTestError(Exception):
    pass


class NotFoundTestError(Exception):
    """A caller's own mistake (BUSINESS)."""


class DeniedTestError(Exception):
    """The caller may not do this (AUTH)."""


class RetryInput(BaseModel):
    action: str = "retry"
    value: int = 1


class RetryOutput(BaseModel):
    attempts: int


class FlakyConnector(BaseConnector):
    connector_id = "test_flaky_resilience"
    output_model = RetryOutput

    def __init__(self) -> None:
        super().__init__()
        self.calls_by_tenant: dict[str, int] = {}
        self.failures_by_tenant: dict[str, int] = {}

    @nw_action("retry")
    async def retry(self, params: RetryInput, *, trace_id: str) -> RetryOutput:
        tenant_key = trace_id.split(":", maxsplit=1)[0]
        self.calls_by_tenant[tenant_key] = self.calls_by_tenant.get(tenant_key, 0) + 1
        failures = self.failures_by_tenant.get(tenant_key, 0)
        if failures > 0:
            self.failures_by_tenant[tenant_key] = failures - 1
            raise RetryableTestError(f"retryable failure for {tenant_key}")
        return RetryOutput(attempts=self.calls_by_tenant[tenant_key])

    async def run_for_tenant(self, tenant_id: str) -> object:
        original_internal_execute = self.internal_execute

        async def _tagged_internal_execute(params: object, *, trace_id: str) -> object:
            return await original_internal_execute(params, trace_id=f"{tenant_id}:{trace_id}")

        self.internal_execute = _tagged_internal_execute  # type: ignore[method-assign]
        try:
            return await self.run({"action": "retry", "value": 1}, tenant_id=tenant_id)
        finally:
            self.internal_execute = original_internal_execute  # type: ignore[method-assign]


class ProbeInput(BaseModel):
    action: str = "go"
    mode: str = "ok"


class BreakerProbe(BaseConnector):
    """One action that fails the way ``mode`` says; counts every attempt it makes."""

    connector_id = "test_breaker_probe"
    output_model = RetryOutput

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    @nw_action("go")
    async def go(self, params: ProbeInput, *, trace_id: str) -> RetryOutput:
        self.attempts += 1
        mode = params.mode
        if mode == "notfound":
            raise NotFoundTestError("no such thing")
        if mode == "denied":
            raise DeniedTestError("not allowed")
        if mode == "timeout":
            raise RetryableTestError("timed out")
        if mode == "connect":
            raise httpx.ConnectError("refused")
        if mode == "oserror":
            raise OSError("connection reset")
        if mode == "bug":
            raise RuntimeError("unmapped bug")
        if mode == "slow":
            await asyncio.sleep(10)
        return RetryOutput(attempts=self.attempts)


@pytest.fixture(autouse=True)
def reset_error_mapper_registry() -> None:
    original_global = dict(ErrorMapper._global_registry)
    original_connectors = {k: dict(v) for k, v in ErrorMapper._connector_registries.items()}
    try:
        ErrorMapper._global_registry.clear()
        ErrorMapper._connector_registries.clear()
        ErrorMapper.register(
            "test_flaky_resilience",
            RetryableTestError,
            ErrorCategory.RETRYABLE,
            code="RETRYABLE_TEST",
        )
        # The decorator-only tests below pass no connector id; an unmapped error is FATAL and does
        # not count against the breaker, so map this one globally as the upstream failure it models.
        ErrorMapper.register_global(
            RetryableTestError, ErrorCategory.RETRYABLE, code="RETRYABLE_TEST"
        )
        ErrorMapper.register(
            "test_breaker_probe", NotFoundTestError, ErrorCategory.BUSINESS, code="NOT_FOUND"
        )
        ErrorMapper.register(
            "test_breaker_probe", DeniedTestError, ErrorCategory.AUTH, code="DENIED"
        )
        yield
    finally:
        ErrorMapper._global_registry.clear()
        ErrorMapper._global_registry.update(original_global)
        ErrorMapper._connector_registries.clear()
        ErrorMapper._connector_registries.update(original_connectors)


def test_with_resilience_retries_retryable_errors_until_success() -> None:
    connector = FlakyConnector()
    connector.failures_by_tenant["tenant-a"] = 2

    response = asyncio.run(connector.run_for_tenant("tenant-a"))

    assert response.success is True
    assert response.data == {"attempts": 3}
    assert connector.calls_by_tenant["tenant-a"] == 3


def test_tenant_breaker_state_is_isolated_across_shared_connector_instance() -> None:
    connector = FlakyConnector()
    connector._breaker_for_tenant("tenant-a").open()

    first = asyncio.run(connector.run_for_tenant("tenant-a"))
    other_tenant = asyncio.run(connector.run_for_tenant("tenant-b"))

    assert first.success is False
    assert first.error_code == "CircuitBreakerError"
    assert first.error_category == ErrorCategory.FATAL
    assert other_tenant.success is True
    assert other_tenant.data == {"attempts": 1}


def test_breaker_cache_uses_distinct_keys_per_tenant() -> None:
    connector = FlakyConnector()

    default_breaker = connector._breaker_for_tenant(None)
    tenant_a_breaker = connector._breaker_for_tenant("tenant-a")
    tenant_b_breaker = connector._breaker_for_tenant("tenant-b")

    assert default_breaker is connector._breaker_for_tenant(None)
    assert tenant_a_breaker is connector._breaker_for_tenant("tenant-a")
    assert tenant_a_breaker is not tenant_b_breaker
    assert default_breaker is not tenant_a_breaker


def test_open_breaker_rejects_calls_immediately() -> None:
    connector = FlakyConnector()
    breaker = connector._breaker_for_tenant("tenant-a")
    breaker.open()

    response = asyncio.run(connector.run_for_tenant("tenant-a"))

    assert response.success is False
    assert response.error_code == "CircuitBreakerError"


def test_circuit_breaker_error_defaults_to_fatal_mapping() -> None:
    mapped = ErrorMapper.resolve(CircuitBreakerError("open"), connector_id="test_flaky_resilience")

    assert mapped.code == "CircuitBreakerError"
    assert mapped.category == ErrorCategory.FATAL


def test_resolve_breaker_returns_same_instance_for_circuit_breaker() -> None:
    cb = CircuitBreaker()
    assert _resolve_breaker(cb) is cb


def test_resolve_breaker_invokes_factory_callable() -> None:
    created = CircuitBreaker()

    def factory() -> CircuitBreaker:
        return created

    assert _resolve_breaker(factory) is created


def test_resolve_breaker_resolved_object_has_state() -> None:
    cb = CircuitBreaker()
    resolved = _resolve_breaker(cb)
    assert hasattr(resolved, "state")
    assert resolved.state.name in ("closed", "open", "half-open")


def test_with_resilience_accepts_concrete_circuit_breaker_instance() -> None:
    breaker = CircuitBreaker()

    @with_resilience(breaker)
    async def succeed(*, trace_id: str = "t") -> str:
        return "ok"

    assert asyncio.run(succeed(trace_id="x")) == "ok"


def test_fatal_errors_do_not_retry() -> None:
    class FatalConnector(BaseConnector):
        connector_id = "test_fatal_resilience"
        output_model = RetryOutput

        @nw_action("retry")
        async def retry(self, params: RetryInput, *, trace_id: str) -> RetryOutput:
            raise FatalTestError("fatal")

    connector = FatalConnector()
    response = asyncio.run(connector.run({"action": "retry", "value": 1}, tenant_id="tenant-a"))

    assert response.success is False
    assert response.error_code == "FatalTestError"
    assert response.error_category == ErrorCategory.FATAL


def test_with_resilience_opens_breaker_after_repeated_failures() -> None:
    breaker = CircuitBreaker(fail_max=2, reset_timeout=30)

    @with_resilience(breaker, max_attempts=1)
    async def fail(*, trace_id: str = "t") -> str:
        raise RetryableTestError("boom")

    with pytest.raises(RetryableTestError):
        asyncio.run(fail(trace_id="t1"))

    with pytest.raises(RetryableTestError):  # the failure that opens it reports its own error
        asyncio.run(fail(trace_id="t2"))

    assert breaker.state.name == "open"

    with pytest.raises(CircuitBreakerError):
        asyncio.run(fail(trace_id="t3"))


def test_with_resilience_success_resets_failure_counter() -> None:
    """A successful call after a failure resets the breaker's failure counter."""
    breaker = CircuitBreaker(fail_max=3, reset_timeout=30)
    state = {"fail": True}

    @with_resilience(breaker, max_attempts=1)
    async def sometimes(*, trace_id: str = "t") -> str:
        if state["fail"]:
            raise RetryableTestError("boom")
        return "ok"

    with pytest.raises(RetryableTestError):
        asyncio.run(sometimes(trace_id="t1"))
    assert breaker.fail_counter == 1

    state["fail"] = False
    assert asyncio.run(sometimes(trace_id="t2")) == "ok"
    assert breaker.fail_counter == 0
    assert breaker.state.name == "closed"


def test_open_breaker_allows_one_trial_after_reset_timeout() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    calls = {"n": 0}

    @with_resilience(breaker, max_attempts=1)
    async def call(*, trace_id: str = "t") -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableTestError("down")
        return "ok"

    async def scenario() -> None:
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await call(trace_id="t1")
        assert breaker.state.name == "open"
        assert calls["n"] == 1

        with pytest.raises(CircuitBreakerError):
            await call(trace_id="t2")
        assert calls["n"] == 1

        await asyncio.sleep(0.08)
        assert await call(trace_id="t3") == "ok"
        assert breaker.state.name == "closed"
        assert breaker.fail_counter == 0
        assert await call(trace_id="t4") == "ok"

    asyncio.run(scenario())


def test_failed_trial_reopens_breaker_and_restarts_timeout() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    calls = {"n": 0}

    @with_resilience(breaker, max_attempts=1)
    async def call(*, trace_id: str = "t") -> str:
        calls["n"] += 1
        raise RetryableTestError("still down")

    async def scenario() -> None:
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await call(trace_id="t1")
        await asyncio.sleep(0.08)
        with pytest.raises(RetryableTestError):
            await call(trace_id="t2")
        assert calls["n"] == 2
        assert breaker.state.name == "open"
        with pytest.raises(CircuitBreakerError):
            await call(trace_id="t3")
        assert calls["n"] == 2

    asyncio.run(scenario())


def test_half_open_allows_only_one_trial() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    calls = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def call(*, trace_id: str = "t") -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RetryableTestError("down")
        started.set()
        await release.wait()
        return "ok"

    async def scenario() -> None:
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await call(trace_id="trip")
        await asyncio.sleep(0.08)
        tasks = [asyncio.create_task(call(trace_id=f"c{i}")) for i in range(5)]
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        rejected = [task for task in tasks if task.done()]
        assert len(rejected) == 4
        for task in rejected:
            with pytest.raises(CircuitBreakerError):
                task.result()
        assert calls["n"] == 2
        release.set()
        pending = [task for task in tasks if not task.done()]
        assert len(pending) == 1
        assert await pending[0] == "ok"
        assert breaker.state.name == "closed"
        assert calls["n"] == 2

    asyncio.run(scenario())


def test_late_success_does_not_close_an_open_breaker() -> None:
    """A slow call that started before the trip cannot skip the reset_timeout cool-down."""
    breaker = CircuitBreaker(fail_max=1, reset_timeout=60)
    release = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def slow(*, trace_id: str = "t") -> str:
        await release.wait()
        return "ok"

    @with_resilience(breaker, max_attempts=1)
    async def failing(*, trace_id: str = "t") -> str:
        raise RetryableTestError("down")

    async def scenario() -> None:
        straggler = asyncio.create_task(slow(trace_id="slow"))
        await asyncio.sleep(0)
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await failing(trace_id="trip")
        release.set()
        assert await straggler == "ok"
        assert breaker.state.name == "open"

    asyncio.run(scenario())


def test_late_success_does_not_close_a_half_open_breaker() -> None:
    """Only the trial can close a half-open breaker."""
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    release_straggler = asyncio.Event()
    release_trial = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def wait_for(event: asyncio.Event, *, trace_id: str = "t") -> str:
        await event.wait()
        return "ok"

    @with_resilience(breaker, max_attempts=1)
    async def failing(*, trace_id: str = "t") -> str:
        raise RetryableTestError("down")

    async def scenario() -> None:
        straggler = asyncio.create_task(wait_for(release_straggler, trace_id="slow"))
        await asyncio.sleep(0)
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await failing(trace_id="trip")
        await asyncio.sleep(0.08)
        trial = asyncio.create_task(wait_for(release_trial, trace_id="trial"))
        await asyncio.sleep(0)
        assert breaker.state.name == "half-open"

        release_straggler.set()
        assert await straggler == "ok"
        assert breaker.state.name == "half-open"

        release_trial.set()
        assert await trial == "ok"
        assert breaker.state.name == "closed"

    asyncio.run(scenario())


def test_late_failure_keeps_timeout_and_its_own_exception() -> None:
    """A slow failure that lands while open neither restarts the timer nor becomes CircuitBreakerError."""
    breaker = CircuitBreaker(fail_max=1, reset_timeout=60)
    release = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def slow_failing(*, trace_id: str = "t") -> str:
        await release.wait()
        raise RetryableTestError("late")

    @with_resilience(breaker, max_attempts=1)
    async def failing(*, trace_id: str = "t") -> str:
        raise RetryableTestError("down")

    async def scenario() -> None:
        straggler = asyncio.create_task(slow_failing(trace_id="slow"))
        await asyncio.sleep(0)
        with pytest.raises(RetryableTestError, match="down"):  # the failure that opens it
            await failing(trace_id="trip")
        opened_at = breaker._opened_at
        release.set()
        with pytest.raises(RetryableTestError, match="late"):
            await asyncio.wait_for(straggler, timeout=1)
        assert breaker._opened_at == opened_at
        assert breaker.state.name == "open"

    asyncio.run(scenario())


def test_hung_trial_is_replaced_after_reset_timeout() -> None:
    """A trial that never finishes cannot hold the breaker half-open forever."""
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    release = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def hung(*, trace_id: str = "t") -> str:
        await release.wait()
        raise RetryableTestError("late")

    @with_resilience(breaker, max_attempts=1)
    async def failing(*, trace_id: str = "t") -> str:
        raise RetryableTestError("down")

    @with_resilience(breaker, max_attempts=1)
    async def ok(*, trace_id: str = "t") -> str:
        return "ok"

    async def scenario() -> None:
        with pytest.raises(RetryableTestError):  # the failure that opens it
            await failing(trace_id="trip")
        await asyncio.sleep(0.08)
        trial = asyncio.create_task(hung(trace_id="trial"))
        await asyncio.sleep(0)
        assert breaker.state.name == "half-open"
        with pytest.raises(CircuitBreakerError):
            await ok(trace_id="during")

        await asyncio.sleep(0.08)
        assert await ok(trace_id="replacement") == "ok"
        assert breaker.state.name == "closed"

        release.set()
        with pytest.raises(RetryableTestError, match="late"):
            await asyncio.wait_for(trial, timeout=1)
        assert breaker.state.name == "closed"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# The breaker counts upstream-health failures, once per invocation (CNP-114)
# --------------------------------------------------------------------------- #


@pytest.fixture
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real retry count (3) but not the real 0.5 s / 1 s waits."""
    monkeypatch.setattr(
        "node_wire_runtime.base_connector.with_resilience",
        partial(with_resilience, base_wait=0.001, max_wait=0.002),
    )


async def _go(connector: BreakerProbe, mode: str) -> object:
    return await connector.run({"action": "go", "mode": mode}, tenant_id="t1")


def _breaker(connector: BreakerProbe) -> CircuitBreaker:
    return connector._breaker_for_tenant("t1")


async def test_v2_2_case_a_business_and_auth_failures_never_trip_the_breaker() -> None:
    connector = BreakerProbe()

    for _ in range(10):
        assert (await _go(connector, "notfound")).error_code == "NOT_FOUND"
    for _ in range(10):
        assert (await _go(connector, "denied")).error_code == "DENIED"

    # Every caller got its own error back (never CircuitBreakerError) and none was retried.
    assert connector.attempts == 20
    assert _breaker(connector).state.name == "closed"
    assert _breaker(connector).fail_counter == 0
    assert (await _go(connector, "ok")).success is True


@pytest.mark.usefixtures("fast_retries")
async def test_v2_2_case_b_an_invocation_that_exhausts_its_retries_counts_once() -> None:
    connector = BreakerProbe()

    first = await _go(connector, "timeout")

    assert first.error_code == "RETRYABLE_TEST"  # its own error, not CircuitBreakerError
    assert connector.attempts == 3  # all three attempts still ran
    assert _breaker(connector).fail_counter == 1  # but it is one failure

    await _go(connector, "timeout")  # the old probe opened the breaker here (3 + 2 attempts)
    assert connector.attempts == 6
    assert _breaker(connector).fail_counter == 2
    assert _breaker(connector).state.name == "closed"


@pytest.mark.usefixtures("fast_retries")
async def test_breaker_opens_after_fail_max_failed_invocations() -> None:
    connector = BreakerProbe()

    for _ in range(4):
        await _go(connector, "timeout")
    assert _breaker(connector).state.name == "closed"

    await _go(connector, "timeout")
    assert _breaker(connector).state.name == "open"
    assert connector.attempts == 15  # five invocations of three attempts each


@pytest.mark.parametrize(
    ("mode", "counted"),
    [("connect", 1), ("oserror", 1), ("bug", 0)],
)
async def test_only_transport_level_fatal_errors_count(mode: str, counted: int) -> None:
    connector = BreakerProbe()

    response = await _go(connector, mode)

    assert response.success is False
    assert connector.attempts == 1  # FATAL is not retried
    assert _breaker(connector).fail_counter == counted


@pytest.mark.usefixtures("fast_retries")
async def test_an_error_that_does_not_count_resets_the_consecutive_count() -> None:
    connector = BreakerProbe()

    await _go(connector, "timeout")
    await _go(connector, "timeout")
    assert _breaker(connector).fail_counter == 2

    await _go(connector, "notfound")  # the upstream answered
    assert _breaker(connector).fail_counter == 0

    await _go(connector, "timeout")
    assert _breaker(connector).fail_counter == 1


async def test_a_trial_that_ends_in_a_business_error_closes_the_breaker() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    results = ["down", "not-found"]

    @with_resilience(breaker, max_attempts=1, connector_id="test_breaker_probe")
    async def call(*, trace_id: str = "t") -> str:
        outcome = results.pop(0)
        raise RetryableTestError(outcome) if outcome == "down" else NotFoundTestError(outcome)

    with pytest.raises(RetryableTestError):  # the failure that trips it reports its own error
        await call(trace_id="t1")
    assert breaker.state.name == "open"

    await asyncio.sleep(0.08)
    with pytest.raises(NotFoundTestError):  # the trial: the upstream answered
        await call(trace_id="t2")

    assert breaker.state.name == "closed"
    assert breaker.fail_counter == 0


async def test_a_cancelled_call_does_not_count_against_the_breaker() -> None:
    connector = BreakerProbe()
    task = asyncio.create_task(_go(connector, "slow"))
    await asyncio.sleep(0.05)  # inside the action

    task.cancel()
    await asyncio.wait({task})

    assert task.cancelled()
    assert _breaker(connector).fail_counter == 0
    assert _breaker(connector).state.name == "closed"


async def test_an_open_breaker_rejects_without_running_or_retrying() -> None:
    breaker = CircuitBreaker()
    breaker.open()
    calls = {"n": 0}

    @with_resilience(breaker)
    async def call(*, trace_id: str = "t") -> str:
        calls["n"] += 1
        return "unreachable"

    with pytest.raises(CircuitBreakerError):
        await call(trace_id="t1")

    assert calls["n"] == 0


# --- configuration ---------------------------------------------------------------------


def test_breaker_defaults_are_five_failures_and_thirty_seconds() -> None:
    breaker = _breaker(BreakerProbe())

    assert (breaker.fail_max, breaker.reset_timeout) == (5, 30)


def test_breaker_class_attributes_override_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 2)
    monkeypatch.setattr(BreakerProbe, "breaker_reset_timeout", 7.5)

    breaker = _breaker(BreakerProbe())

    assert (breaker.fail_max, breaker.reset_timeout) == (2, 7.5)


async def test_a_lower_fail_max_opens_the_breaker_sooner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 2)
    connector = BreakerProbe()

    await _go(connector, "connect")
    assert _breaker(connector).state.name == "closed"
    await _go(connector, "connect")

    assert _breaker(connector).state.name == "open"


def test_breaker_env_overrides_the_class_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 2)
    monkeypatch.setenv("NW_BREAKER_TEST_BREAKER_PROBE_FAIL_MAX", "9")
    monkeypatch.setenv("NW_BREAKER_TEST_BREAKER_PROBE_RESET_TIMEOUT", "0.25")

    breaker = _breaker(BreakerProbe())

    assert (breaker.fail_max, breaker.reset_timeout) == (9, 0.25)


@pytest.mark.parametrize(
    ("suffix", "value"),
    [
        ("FAIL_MAX", "0"),
        ("FAIL_MAX", "-1"),
        ("FAIL_MAX", "abc"),
        ("FAIL_MAX", "1.5"),
        ("RESET_TIMEOUT", "0"),
        ("RESET_TIMEOUT", "nan"),
        ("RESET_TIMEOUT", "soon"),
    ],
)
def test_a_bad_breaker_setting_fails_when_the_connector_is_built(
    monkeypatch: pytest.MonkeyPatch, suffix: str, value: str
) -> None:
    name = f"NW_BREAKER_TEST_BREAKER_PROBE_{suffix}"
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=f"{name} must be a positive number"):
        BreakerProbe()


def test_a_bad_class_attribute_fails_when_the_connector_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 0)

    with pytest.raises(ValueError, match="NW_BREAKER_TEST_BREAKER_PROBE_FAIL_MAX"):
        BreakerProbe()


# --------------------------------------------------------------------------- #
# A cancelled half-open trial hands its slot to the next caller
# --------------------------------------------------------------------------- #


def _trial_probe(breaker: CircuitBreaker):
    """A wrapped call whose behaviour is switched through the returned ``mode`` dict."""
    mode = {"v": "fail"}
    gate = asyncio.Event()

    @with_resilience(breaker, max_attempts=1)
    async def call(*, trace_id: str = "t") -> str:
        if mode["v"] == "fail":
            raise RetryableTestError("down")
        if mode["v"] == "hang":
            await asyncio.sleep(10)
        if mode["v"] == "gate":
            await gate.wait()
        return "ok"

    return call, mode, gate


async def _open_and_wait_out_the_timeout(call, breaker: CircuitBreaker) -> None:
    # The call that trips the breaker reports its own error.
    with contextlib.suppress(RetryableTestError):
        await call(trace_id="trip")
    assert breaker.state.name == "open"
    await asyncio.sleep(breaker.reset_timeout + 0.03)


async def test_a_cancelled_trial_hands_the_slot_to_the_next_caller() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    call, mode, _ = _trial_probe(breaker)
    await _open_and_wait_out_the_timeout(call, breaker)

    mode["v"] = "hang"
    trial = asyncio.create_task(call(trace_id="trial"))
    await asyncio.sleep(0)  # the trial is under way
    assert breaker.state.name == "half-open"

    trial.cancel()  # the client disconnects
    await asyncio.wait({trial})

    assert trial.cancelled()
    assert breaker.state.name == "open"  # not stuck half-open
    mode["v"] = "ok"
    assert await call(trace_id="next") == "ok"  # no second reset_timeout to wait out
    assert breaker.state.name == "closed"


async def test_a_trial_stopped_by_asyncio_timeout_releases_the_slot() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    call, mode, _ = _trial_probe(breaker)
    await _open_and_wait_out_the_timeout(call, breaker)

    mode["v"] = "hang"
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await call(trace_id="trial")

    assert breaker.state.name == "open"
    mode["v"] = "ok"
    assert await call(trace_id="next") == "ok"


async def test_cancelling_a_call_in_a_closed_breaker_changes_nothing() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=30)
    call, mode, _ = _trial_probe(breaker)
    mode["v"] = "hang"
    task = asyncio.create_task(call(trace_id="t"))
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.wait({task})

    assert task.cancelled()
    assert breaker.state.name == "closed"
    assert breaker.fail_counter == 0


async def test_cancelling_a_replaced_trial_leaves_the_new_trial_alone() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)
    call, mode, gate = _trial_probe(breaker)
    await _open_and_wait_out_the_timeout(call, breaker)

    mode["v"] = "hang"
    first = asyncio.create_task(call(trace_id="first"))
    await asyncio.sleep(0)  # the first trial is under way
    await asyncio.sleep(breaker.reset_timeout + 0.03)  # it outlives reset_timeout...
    mode["v"] = "gate"
    second = asyncio.create_task(call(trace_id="second"))
    await asyncio.sleep(0)  # ...so the second call replaces it as the trial
    assert breaker.state.name == "half-open"

    first.cancel()  # the stale trial is cancelled
    await asyncio.wait({first})

    assert breaker.state.name == "half-open"  # the second trial still holds the slot
    with pytest.raises(CircuitBreakerError):
        await call(trace_id="during")
    gate.set()
    assert await second == "ok"
    assert breaker.state.name == "closed"


def test_release_only_applies_to_the_live_half_open_trial() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.01)

    closed_generation = breaker.allow()
    breaker.release(closed_generation)  # closed: nothing to release
    assert breaker.state.name == "closed"
    assert breaker.fail_counter == 0

    breaker.open()
    breaker.release(closed_generation)  # open, and a stale generation anyway
    assert breaker.state.name == "open"

    time.sleep(0.02)
    trial = breaker.allow()  # half-open: this call is the live trial
    assert breaker.state.name == "half-open"
    breaker.release(trial)
    assert breaker.state.name == "open"
    assert breaker.allow() == trial + 1  # the next caller becomes the trial immediately

    breaker.release(trial)  # that trial is now a stale generation
    assert breaker.state.name == "half-open"


async def test_a_retry_loop_that_yields_no_attempt_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard after the loop: tenacity should always return or raise, never just stop."""

    class _NoAttempts:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __aiter__(self) -> "_NoAttempts":
            return self

        async def __anext__(self) -> None:
            raise StopAsyncIteration

    monkeypatch.setattr("node_wire_runtime.resilience.AsyncRetrying", _NoAttempts)

    @with_resilience(CircuitBreaker())
    async def call(*, trace_id: str = "t") -> str:
        return "unreachable"

    with pytest.raises(RuntimeError, match="Exhausted retries without success"):
        await call(trace_id="t")


# --------------------------------------------------------------------------- #
# Opening the breaker keeps the failing call's own error (CNP-115)
# --------------------------------------------------------------------------- #


class _ExitLike(BaseException):
    """Stands in for KeyboardInterrupt / SystemExit, which asyncio treats specially."""


async def test_v2_3_case_d_a_cancelled_run_is_not_an_envelope_and_does_not_open_a_fail_max_1_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 1)
    connector = BreakerProbe()
    task = asyncio.create_task(_go(connector, "slow"))
    await asyncio.sleep(0.05)  # inside the action

    task.cancel()
    await asyncio.wait({task})

    assert task.cancelled()  # CancelledError, not a FATAL ConnectorResponse
    assert _breaker(connector).fail_counter == 0
    assert _breaker(connector).state.name == "closed"


async def test_v2_3_case_d2_asyncio_timeout_raises_timeout_error_and_is_not_counted() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=30)
    call, mode, _ = _trial_probe(breaker)
    mode["v"] = "hang"

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await call(trace_id="t")

    assert breaker.fail_counter == 0
    assert breaker.state.name == "closed"


async def test_v2_3_case_e_the_call_that_opens_the_breaker_reports_its_own_error() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=30)
    call, _, _ = _trial_probe(breaker)

    with pytest.raises(RetryableTestError, match="down"):
        await call(trace_id="opens")
    assert breaker.state.name == "open"

    with pytest.raises(CircuitBreakerError):  # only the next call sees the open circuit
        await call(trace_id="next")


async def test_v2_3_case_e_through_run_the_threshold_caller_gets_its_own_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(BreakerProbe, "breaker_fail_max", 1)
    connector = BreakerProbe()

    opens = await _go(connector, "oserror")  # transport-level FATAL: counts, is not retried
    rejected = await _go(connector, "oserror")

    assert opens.error_code == "OSError"
    assert opens.error_category == ErrorCategory.FATAL
    assert rejected.error_code == "CircuitBreakerError"
    assert connector.attempts == 1  # the rejected call never reached the action


async def test_a_failure_that_is_not_an_exception_propagates_unchanged_and_is_not_counted() -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=30)

    @with_resilience(breaker, max_attempts=1)
    async def call(*, trace_id: str = "t") -> str:
        raise _ExitLike

    with pytest.raises(_ExitLike):
        await call(trace_id="t")

    assert breaker.fail_counter == 0
    assert breaker.state.name == "closed"


def test_record_failure_at_the_threshold_opens_the_breaker_without_raising() -> None:
    breaker = CircuitBreaker(fail_max=2, reset_timeout=30)
    generation = breaker.allow()

    assert breaker.record_failure(generation) is None
    assert breaker.state.name == "closed"
    assert breaker.record_failure(generation) is None
    assert breaker.state.name == "open"


def test_the_breaker_logs_a_warning_when_it_opens(caplog: pytest.LogCaptureFixture) -> None:
    breaker = CircuitBreaker(fail_max=1, reset_timeout=30, name="probe_breaker")

    with caplog.at_level("WARNING", logger="runtime.resilience"):
        breaker.record_failure(breaker.allow())

    opened = [r for r in caplog.records if r.getMessage() == "Circuit breaker opened"]
    assert len(opened) == 1
    assert (opened[0].breaker, opened[0].fail_max, opened[0].reset_timeout) == (
        "probe_breaker",
        1,
        30,
    )
