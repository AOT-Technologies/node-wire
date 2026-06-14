#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import asyncio
import ipaddress
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from node_wire_http_generic.egress import (
    HttpEgressBlockedError,
    build_pinned_request_kwargs,
    build_pinned_url,
    is_blocked_address,
    normalize_ip,
    validate_egress_url,
    validate_host_literal,
)
from node_wire_http_generic.logic import HttpGenericConnector
from node_wire_http_generic.schema import HttpRequestInput


def test_normalize_ip_unwraps_ipv4_mapped() -> None:
    mapped = ipaddress.ip_address("::ffff:127.0.0.1")
    assert str(normalize_ip(mapped)) == "127.0.0.1"
    assert is_blocked_address(normalize_ip(mapped))


def test_validate_host_literal_blocks_ipv4_mapped_loopback() -> None:
    with pytest.raises(HttpEgressBlockedError):
        validate_host_literal("::ffff:127.0.0.1")


def test_validate_host_literal_blocks_decimal_encoding() -> None:
    with pytest.raises(HttpEgressBlockedError, match="numeric encoding"):
        validate_host_literal("2130706433")


def test_validate_host_literal_blocks_octal_encoding() -> None:
    with pytest.raises(HttpEgressBlockedError, match="numeric encoding"):
        validate_host_literal("0177.0.0.1")


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://localhost/health",
        "http://127.0.0.1/internal",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/health",
        "http://[::ffff:127.0.0.1]/health",
        "http://metadata.google.internal/computeMetadata/v1",
    ],
)
def test_http_request_input_rejects_internal_targets(blocked_url: str) -> None:
    with pytest.raises(ValidationError):
        HttpRequestInput(url=blocked_url, method="GET")


def test_http_request_input_allows_public_url() -> None:
    parsed = HttpRequestInput(url="https://example.com/path?q=1", method="GET")
    assert str(parsed.url) == "https://example.com/path?q=1"


@pytest.mark.asyncio
async def test_validate_egress_url_blocks_dns_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(HttpEgressBlockedError, match="blocked network target"):
        await validate_egress_url("https://evil.example/path")


@pytest.mark.asyncio
async def test_validate_egress_url_blocks_dual_stack_with_private_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(HttpEgressBlockedError, match="blocked network target"):
        await validate_egress_url("https://dual.example/path")


@pytest.mark.asyncio
async def test_validate_egress_url_allowlist_blocks_unknown_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_HTTP_GENERIC_EGRESS_ALLOWLIST", "httpbin.org")
    with pytest.raises(HttpEgressBlockedError, match="egress allowlist"):
        await validate_egress_url("https://example.com/path")


@pytest.mark.asyncio
async def test_validate_egress_url_allowlist_allows_listed_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_HTTP_GENERIC_EGRESS_ALLOWLIST", "httpbin.org")

    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("54.230.0.1", 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    validated = await validate_egress_url("https://httpbin.org/post")
    assert validated.hostname == "httpbin.org"
    assert validated.pinned_ips[0] == ipaddress.ip_address("54.230.0.1")


def test_build_pinned_request_kwargs_sets_host_and_sni() -> None:
    from node_wire_http_generic.egress import ValidatedEgress

    validated = ValidatedEgress(
        original_url="https://example.com/path",
        hostname="example.com",
        port=443,
        explicit_port=None,
        scheme="https",
        path="/path",
        query="",
        pinned_ips=(ipaddress.ip_address("93.184.216.34"),),
    )
    kwargs = build_pinned_request_kwargs(validated, base_headers={"X-Test": "1"})
    assert kwargs["url"] == "https://93.184.216.34/path"
    assert kwargs["headers"]["Host"] == "example.com"
    assert kwargs["headers"]["X-Test"] == "1"
    assert kwargs["extensions"] == {"sni_hostname": "example.com"}
    assert kwargs["follow_redirects"] is False


def test_build_pinned_url_preserves_explicit_port() -> None:
    from node_wire_http_generic.egress import ValidatedEgress

    validated = ValidatedEgress(
        original_url="http://example.com:8080/path",
        hostname="example.com",
        port=8080,
        explicit_port=8080,
        scheme="http",
        path="/path",
        query="",
        pinned_ips=(ipaddress.ip_address("93.184.216.34"),),
    )
    assert build_pinned_url(validated, validated.pinned_ips[0]) == "http://93.184.216.34:8080/path"


def test_http_generic_uses_pinned_url_in_request(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = httpx.Headers({})
    mock_resp.text = "ok"
    captured: dict[str, object] = {}

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def request(self, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            return mock_resp

    async def fake_validate(url: str):
        from node_wire_http_generic.egress import ValidatedEgress

        return ValidatedEgress(
            original_url=url,
            hostname="example.com",
            port=443,
            explicit_port=None,
            scheme="https",
            path="/path",
            query="",
            pinned_ips=(ipaddress.ip_address("93.184.216.34"),),
        )

    async def _run() -> None:
        with (
            patch(
                "node_wire_http_generic.logic.validate_egress_url",
                new=AsyncMock(side_effect=fake_validate),
            ),
            patch(
                "node_wire_http_generic.logic.httpx.AsyncClient",
                return_value=_FakeAsyncClient(),
            ),
        ):
            connector = HttpGenericConnector()
            inp = HttpRequestInput(url="https://example.com/path", method="GET")
            out = await connector.internal_execute(inp, trace_id="t-pin")
        assert out.status_code == 200

    asyncio.run(_run())
    assert captured["url"] == "https://93.184.216.34/path"
    assert captured["headers"]["Host"] == "example.com"
    assert captured["follow_redirects"] is False
    assert captured["extensions"] == {"sni_hostname": "example.com"}
