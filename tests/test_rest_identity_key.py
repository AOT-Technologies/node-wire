#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import hashlib

import pytest
from starlette.requests import Request

from bindings.rest_api.auth import get_request_identity_key


def _request(
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    client_host: str = "127.0.0.1",
) -> Request:
    scope = {
        "type": "http",
        "headers": headers or [],
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_identity_key_uses_token_hash_regardless_of_xff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "0")
    token = "secret-api-key"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    request = _request(
        headers=[
            (b"x-api-key", token.encode("utf-8")),
            (b"x-forwarded-for", b"203.0.113.99"),
        ],
    )
    assert get_request_identity_key(request) == f"token:{digest}"


def test_identity_key_ignores_spoofed_xff_when_proxy_hops_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "0")
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.99")],
        client_host="10.0.0.5",
    )
    assert get_request_identity_key(request) == "ip:10.0.0.5"


def test_identity_key_uses_xff_when_trusted_proxy_hops_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "1")
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        client_host="10.0.0.5",
    )
    assert get_request_identity_key(request) == "ip:203.0.113.5"


def test_identity_key_falls_back_to_client_host_when_xff_too_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "2")
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.5")],
        client_host="10.0.0.5",
    )
    assert get_request_identity_key(request) == "ip:10.0.0.5"


def test_identity_key_treats_invalid_proxy_hops_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_REST_TRUSTED_PROXY_HOPS", "not-a-number")
    request = _request(
        headers=[(b"x-forwarded-for", b"203.0.113.5")],
        client_host="10.0.0.5",
    )
    assert get_request_identity_key(request) == "ip:10.0.0.5"
