#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Writes whose outcome is unknown are not retried (``OUTCOME_UNKNOWN``).

A timeout or 5xx after a POST/PATCH may have been applied upstream; retrying could apply it
twice. Requests that never left (connect errors) and 429s stay retryable for every method.
"""

from __future__ import annotations

from functools import partial
from typing import Any, ClassVar, Dict, Literal, Optional, Tuple, Type
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel

from node_wire_runtime import ErrorCategory, nw_action
from node_wire_runtime.errors import CATALOGUE, ErrorCode, ErrorMapper, OutcomeUnknownError
from node_wire_runtime.resilience import CircuitBreaker, request_is_idempotent, with_resilience
from node_wire_runtime.rest import RestConnector, RestResponseOutput


class _In(BaseModel):
    action: Literal["create"] = "create"


class _ReadIn(BaseModel):
    action: Literal["read"] = "read"


class _KeyedIn(BaseModel):
    action: Literal["create_keyed"] = "create_keyed"


class _DeclaredIn(BaseModel):
    action: Literal["upsert"] = "upsert"


class _Gen(RestConnector):
    """Shaped like a generated connector: timeouts and connect errors mapped RETRYABLE."""

    connector_id = "test_outcome_unknown_rest"
    default_base_url = "https://api.example.com"
    output_model: ClassVar[Type[BaseModel]] = RestResponseOutput
    _nw_abstract_base = False

    error_map: ClassVar[Dict[Type[BaseException], Tuple[ErrorCategory, str]]] = {
        httpx.TimeoutException: (ErrorCategory.RETRYABLE, "HTTP_TIMEOUT"),
        httpx.ConnectError: (ErrorCategory.RETRYABLE, "HTTP_CONNECT_ERROR"),
        httpx.HTTPStatusError: (ErrorCategory.BUSINESS, "HTTP_STATUS_ERROR"),
    }

    @nw_action("create", requires_auth=False)
    async def create(self, params: _In, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            method="POST",
            path_template="/things",
            params=params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
            auth=False,
        )

    @nw_action("read", requires_auth=False)
    async def read(self, params: _ReadIn, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            method="GET",
            path_template="/things",
            params=params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
            auth=False,
        )

    @nw_action("create_keyed", requires_auth=False)
    async def create_keyed(self, params: _KeyedIn, *, trace_id: str) -> RestResponseOutput:
        self._static_headers = {"Idempotency-Key": "abc"}
        return await self.execute_rest(
            method="POST",
            path_template="/things",
            params=params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
            auth=False,
        )

    @nw_action("upsert", requires_auth=False, idempotent=True)
    async def upsert(self, params: _DeclaredIn, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            method="POST",
            path_template="/things",
            params=params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
            auth=False,
        )


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "node_wire_runtime.base_connector.with_resilience",
        partial(with_resilience, base_wait=0.001, max_wait=0.002),
    )
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination", AsyncMock(return_value=None)
    )


def _install(monkeypatch: pytest.MonkeyPatch, failure: Any) -> list[httpx.Request]:
    """Every request fails with ``failure`` (an exception class, or a status code)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure, request=request)
        raise failure("boom", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("node_wire_runtime.rest._shared_client", lambda trust_env: client)
    return seen


async def test_post_timeout_is_not_retried_and_reports_outcome_unknown(monkeypatch) -> None:
    seen = _install(monkeypatch, httpx.ReadTimeout)
    resp = await _Gen().run({"action": "create"})
    assert len(seen) == 1
    assert resp.error_code == ErrorCode.OUTCOME_UNKNOWN
    assert resp.error_category is ErrorCategory.FATAL
    assert "POST" in (resp.message or "")


async def test_get_timeout_is_still_retried(monkeypatch) -> None:
    seen = _install(monkeypatch, httpx.ReadTimeout)
    resp = await _Gen().run({"action": "read"})
    assert len(seen) == 3
    assert resp.error_code == "HTTP_TIMEOUT"


async def test_post_connect_error_is_retried(monkeypatch) -> None:
    seen = _install(monkeypatch, httpx.ConnectError)
    resp = await _Gen().run({"action": "create"})
    assert len(seen) == 3
    assert resp.error_code == "HTTP_CONNECT_ERROR"


async def test_post_with_an_idempotency_key_is_retried(monkeypatch) -> None:
    seen = _install(monkeypatch, httpx.ReadTimeout)
    resp = await _Gen().run({"action": "create_keyed"})
    assert len(seen) == 3
    assert resp.error_code == "HTTP_TIMEOUT"


async def test_an_action_declared_idempotent_is_retried(monkeypatch) -> None:
    seen = _install(monkeypatch, httpx.ReadTimeout)
    resp = await _Gen().run({"action": "upsert"})
    assert len(seen) == 3
    assert resp.error_code == "HTTP_TIMEOUT"


async def test_outcome_unknown_counts_against_the_breaker(monkeypatch) -> None:
    _install(monkeypatch, httpx.ReadTimeout)
    connector = _Gen()
    await connector.run({"action": "create"})
    assert connector._breaker_for_tenant(None).fail_counter == 1


# ---- the retry decision, without a connector ----------------------------------------------


class _Retryable(Exception):
    pass


@pytest.fixture
def _mapped() -> Any:
    ErrorMapper.register_global(_Retryable, ErrorCategory.RETRYABLE, code="R")
    ErrorMapper.register_global(httpx.HTTPStatusError, ErrorCategory.RETRYABLE, code="S")
    yield
    ErrorMapper._global_registry.pop(_Retryable, None)
    ErrorMapper._global_registry.pop(httpx.HTTPStatusError, None)


def _status_error(method: str, status: int) -> httpx.HTTPStatusError:
    request = httpx.Request(method, "https://api.example.com/x")
    return httpx.HTTPStatusError(
        "x", request=request, response=httpx.Response(status, request=request)
    )


async def _attempts(exc: Exception, idempotent: Optional[bool] = None) -> tuple[int, Exception]:
    calls = {"n": 0}

    @with_resilience(CircuitBreaker(), base_wait=0.001, max_wait=0.002, idempotent=idempotent)
    async def call(*, trace_id: str = "t") -> None:
        calls["n"] += 1
        raise exc

    with pytest.raises(Exception) as caught:
        await call()
    return calls["n"], caught.value


@pytest.mark.usefixtures("_mapped")
async def test_post_5xx_is_outcome_unknown() -> None:
    n, exc = await _attempts(_status_error("POST", 503))
    assert n == 1
    assert isinstance(exc, OutcomeUnknownError)
    assert isinstance(exc.__cause__, httpx.HTTPStatusError)


@pytest.mark.usefixtures("_mapped")
async def test_patch_5xx_is_outcome_unknown_and_put_5xx_is_retried() -> None:
    assert (await _attempts(_status_error("PATCH", 502)))[0] == 1
    assert (await _attempts(_status_error("PUT", 502)))[0] == 3


@pytest.mark.usefixtures("_mapped")
async def test_429_is_retried_for_a_post() -> None:
    n, exc = await _attempts(_status_error("POST", 429))
    assert n == 3
    assert isinstance(exc, httpx.HTTPStatusError)


@pytest.mark.usefixtures("_mapped")
async def test_a_declared_non_idempotent_action_is_not_retried_without_an_http_request() -> None:
    n, exc = await _attempts(_Retryable("down"), idempotent=False)
    assert n == 1
    assert isinstance(exc, OutcomeUnknownError)


@pytest.mark.usefixtures("_mapped")
async def test_an_undeclared_non_http_failure_keeps_being_retried() -> None:
    n, exc = await _attempts(_Retryable("down"))
    assert n == 3
    assert isinstance(exc, _Retryable)


def test_request_is_idempotent() -> None:
    assert request_is_idempotent("get")
    assert request_is_idempotent("DELETE")
    assert not request_is_idempotent("POST")
    assert not request_is_idempotent("PATCH", {"Content-Type": "application/json"})
    assert request_is_idempotent("POST", {"idempotency-key": "k"})
    assert request_is_idempotent("POST", {"If-None-Exist": "identifier=a|b"})


def test_outcome_unknown_is_in_the_catalogue_and_not_retryable() -> None:
    assert CATALOGUE[ErrorCode.OUTCOME_UNKNOWN] is ErrorCategory.FATAL
