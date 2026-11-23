#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""A REST answer's status decides the error category: 401 auth, 429 retryable, 502/503/504 retryable
on reads and deletes only. Everything else stays a plain HTTPStatusError (BUSINESS)."""

from __future__ import annotations

import importlib

import httpx
import pytest

from node_wire_runtime import RestAuthError, RestRateLimited, RestUnavailable
from node_wire_runtime.errors import ErrorMapper
from node_wire_runtime.models import ErrorCategory
from node_wire_runtime.rest import _status_error


def _response(status: int, method: str) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request(method, "https://api.example.com/v1/x"))


@pytest.mark.parametrize(
    ("status", "method", "expected"),
    [
        (401, "GET", RestAuthError),
        (401, "POST", RestAuthError),
        (429, "POST", RestRateLimited),
        (429, "GET", RestRateLimited),
        (502, "GET", RestUnavailable),
        (503, "get", RestUnavailable),
        (503, "DELETE", RestUnavailable),
        (504, "HEAD", RestUnavailable),
        (503, "OPTIONS", RestUnavailable),
        (502, "POST", httpx.HTTPStatusError),  # a write may already have run
        (503, "PATCH", httpx.HTTPStatusError),
        (504, "PUT", httpx.HTTPStatusError),
        (500, "GET", httpx.HTTPStatusError),
        (402, "POST", httpx.HTTPStatusError),  # Stripe: a declined card
        (403, "GET", httpx.HTTPStatusError),
        (404, "GET", httpx.HTTPStatusError),
        (409, "POST", httpx.HTTPStatusError),
    ],
)
def test_the_status_picks_the_exception_class(
    status: int, method: str, expected: type[Exception]
) -> None:
    response = _response(status, method)
    error = _status_error(method, response)
    assert type(error) is expected
    assert error.response is response


def test_the_message_is_the_one_httpx_would_give() -> None:
    response = _response(404, "GET")
    with pytest.raises(httpx.HTTPStatusError) as plain:
        response.raise_for_status()
    assert str(_status_error("GET", response)) == str(plain.value)


@pytest.mark.parametrize(
    ("module", "connector_id"),
    [
        ("node_wire_github.logic", "github"),
        ("node_wire_slack_api.logic", "slack_api"),
        ("node_wire_stripe.logic", "stripe"),
        ("node_wire_pet_store.logic", "pet_store"),
    ],
)
@pytest.mark.parametrize(
    ("status", "method", "category", "code"),
    [
        (401, "GET", ErrorCategory.AUTH, "HTTP_AUTH_ERROR"),
        (429, "POST", ErrorCategory.RETRYABLE, "HTTP_RATE_LIMITED"),
        (503, "GET", ErrorCategory.RETRYABLE, "HTTP_UNAVAILABLE"),
        (503, "POST", ErrorCategory.BUSINESS, "HTTP_STATUS_ERROR"),
        (404, "GET", ErrorCategory.BUSINESS, "HTTP_STATUS_ERROR"),
    ],
)
def test_the_generated_connectors_map_them(
    module: str, connector_id: str, status: int, method: str, category: ErrorCategory, code: str
) -> None:
    importlib.import_module(module)
    mapped = ErrorMapper.resolve(
        _status_error(method, _response(status, method)), connector_id=connector_id
    )
    assert (mapped.category, mapped.code) == (category, code)
