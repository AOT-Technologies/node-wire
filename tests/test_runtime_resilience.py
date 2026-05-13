from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel
from pybreaker import CircuitBreaker, CircuitBreakerError

from node_wire_runtime import BaseConnector, ErrorCategory, ErrorMapper, nw_action
from node_wire_runtime.resilience import _resolve_breaker, with_resilience


class RetryableTestError(Exception):
    pass


class FatalTestError(Exception):
    pass


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


@pytest.fixture(autouse=True)
def reset_error_mapper_registry() -> None:
    original = dict(ErrorMapper._registry)
    try:
        ErrorMapper._registry.clear()
        ErrorMapper.register(RetryableTestError, ErrorCategory.RETRYABLE, code="RETRYABLE_TEST")
        yield
    finally:
        ErrorMapper._registry.clear()
        ErrorMapper._registry.update(original)


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
    mapped = ErrorMapper.resolve(CircuitBreakerError("open"))

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
