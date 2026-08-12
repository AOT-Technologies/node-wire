#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for RestConnector, SSRF helpers, and ApiKeyQueryAuthProvider."""

from __future__ import annotations

import base64
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import BaseModel, Field

from node_wire_runtime import nw_action
from node_wire_runtime.auth import ApiKeyQueryAuthProvider
from node_wire_runtime.http_safety import (
    SsrfBlockedError,
    assert_safe_destination,
    load_rest_allowed_hosts,
    rest_trust_env,
    sanitize_url_for_log,
)
from node_wire_runtime.rest import (
    RestConnector,
    RestResponseOutput,
    encode_param_value,
    split_params_by_location,
)
from node_wire_runtime.secrets import SecretProvider


class _DictSecrets(SecretProvider):
    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def get_secret(self, key: str) -> str:
        return self._data[key]


class _ListInput(BaseModel):
    action: Literal["list_things"] = "list_things"
    username: str = Field(
        ...,
        json_schema_extra={
            "nw_in": "path",
            "nw_wire_name": "username",
            "nw_style": "simple",
            "nw_explode": False,
        },
    )
    limit: int | None = Field(
        None,
        json_schema_extra={
            "nw_in": "query",
            "nw_wire_name": "limit",
            "nw_style": "form",
            "nw_explode": True,
        },
    )
    x_request_id: str | None = Field(
        None,
        alias="X-Request-Id",
        json_schema_extra={
            "nw_in": "header",
            "nw_wire_name": "X-Request-Id",
            "nw_style": "simple",
            "nw_explode": False,
        },
    )


class _DemoConnector(RestConnector):
    connector_id = "demo_rest"
    default_base_url = "https://api.example.com"
    output_model = RestResponseOutput

    @nw_action("list_things")
    async def list_things(self, params: _ListInput, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET",
            "/users/{username}/things",
            params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
        )


def test_sanitize_url_strips_query() -> None:
    assert sanitize_url_for_log("https://api.example.com/x?token=secret") == (
        "https://api.example.com/x"
    )


def test_load_rest_allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_ALLOWED_HOSTS", raising=False)
    assert load_rest_allowed_hosts() == frozenset()
    monkeypatch.setenv("NW_REST_ALLOWED_HOSTS", "api.example.com, other.example.com")
    assert load_rest_allowed_hosts() == frozenset({"api.example.com", "other.example.com"})


def test_rest_trust_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_REST_TRUST_ENV", raising=False)
    assert rest_trust_env() is False
    monkeypatch.setenv("NW_REST_TRUST_ENV", "true")
    assert rest_trust_env() is True


@pytest.mark.asyncio
async def test_assert_safe_destination_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_REST_ALLOWED_HOSTS", "api.allowed.example.com")

    async def fake_getaddrinfo(host: str, port: int, **kwargs: Any) -> list:
        return [(None, None, None, None, ("93.184.216.34", port))]

    monkeypatch.setattr(
        "node_wire_runtime.http_safety.asyncio.get_event_loop",
        lambda: MagicMock(getaddrinfo=AsyncMock(side_effect=fake_getaddrinfo)),
    )
    with pytest.raises(SsrfBlockedError, match="allowlist"):
        await assert_safe_destination("https://evil.example.com/x")


def test_encode_query_form_explode_array() -> None:
    encoded = encode_param_value(["a", "b"], style="form", explode=True, location="query")
    assert encoded == ["a", "b"]


def test_encode_query_form_no_explode_array() -> None:
    encoded = encode_param_value(["a", "b"], style="form", explode=False, location="query")
    assert encoded == "a,b"


def test_split_params_by_location() -> None:
    params = _ListInput(username="octocat", limit=10, **{"X-Request-Id": "abc"})
    path, query, header, body, media = split_params_by_location(params)
    assert "username" in path
    assert "limit" in query
    assert "X-Request-Id" in header
    assert body is None


@pytest.mark.asyncio
async def test_apikey_query_provider() -> None:
    provider = ApiKeyQueryAuthProvider(
        secret_provider=_DictSecrets({"DEMO_API_KEY": "sekret"}),
        secret_key="DEMO_API_KEY",
        name="api_key",
    )
    assert await provider.get_headers() == {}
    assert await provider.get_query_params() == {"api_key": "sekret"}


@pytest.mark.asyncio
async def test_execute_rest_merges_query_auth_and_sanitizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        content = b'{"ok": true}'
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self) -> dict:
            return {"ok": True}

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _FakeResponse:
            captured["request"] = kwargs
            return _FakeResponse()

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination",
        AsyncMock(return_value=None),
    )

    auth = ApiKeyQueryAuthProvider(
        secret_provider=_DictSecrets({"K": "tok"}),
        secret_key="K",
        name="api_key",
    )
    connector = _DemoConnector(
        secret_provider=_DictSecrets({}),
        auth_provider=auth,
        base_url="https://api.example.com",
    )
    params = _ListInput(username="octocat", limit=5)
    result = await connector.execute_rest(
        "GET",
        "/users/{username}/things",
        params,
        output_model=RestResponseOutput,
        trace_id="t1",
    )
    assert isinstance(result, RestResponseOutput)
    req = captured["request"]
    assert req["url"] == "https://api.example.com/users/octocat/things"
    params_pairs = dict(req["params"])
    assert params_pairs["api_key"] == "tok"
    assert params_pairs["limit"] == "5"
    assert captured["client_kwargs"]["trust_env"] is False
    assert captured["client_kwargs"]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_execute_rest_auth_false_skips_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 204
        content = b""
        headers: dict[str, str] = {}
        text = ""

        def raise_for_status(self) -> None:
            return None

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _FakeResponse:
            captured["request"] = kwargs
            return _FakeResponse()

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination",
        AsyncMock(return_value=None),
    )

    auth = ApiKeyQueryAuthProvider(
        secret_provider=_DictSecrets({"K": "tok"}),
        secret_key="K",
        name="api_key",
    )
    connector = _DemoConnector(auth_provider=auth, base_url="https://api.example.com")
    params = _ListInput(username="octocat")
    await connector.execute_rest(
        "GET",
        "/users/{username}/things",
        params,
        output_model=RestResponseOutput,
        trace_id="t1",
        auth=False,
    )
    pairs = captured["request"]["params"] or []
    assert "api_key" not in dict(pairs)


def test_factory_apikey_query_and_unknown_raises() -> None:
    from bindings.factory import ConnectorFactory

    sp = _DictSecrets({"DEMO_API_KEY": "x"})
    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = sp

    provider = factory._build_auth_provider(
        "demo",
        {"auth": {"provider": "apikey_query", "name": "key", "secret_key": "DEMO_API_KEY"}},
    )
    assert isinstance(provider, ApiKeyQueryAuthProvider)

    with pytest.raises(ValueError, match="Unknown auth provider"):
        factory._build_auth_provider("demo", {"auth": {"provider": "not_a_real_provider"}})


def test_factory_missing_auth_still_no_auth() -> None:
    from bindings.factory import ConnectorFactory
    from node_wire_runtime.auth import NoAuthProvider

    factory = ConnectorFactory.__new__(ConnectorFactory)
    factory._secret_provider = _DictSecrets({})
    provider = factory._build_auth_provider("demo", {})
    assert isinstance(provider, NoAuthProvider)


# ---------------------------------------------------------------------------
# encode_param_value / body encoding / resolve_base_url edge coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "style", "explode", "expected"),
    [
        (["a", "b"], "simple", False, "a%2Cb"),
        ({"k": "v", "x": "y"}, "simple", False, "k%2Cv%2Cx%2Cy"),
        ({"k": "v"}, "simple", True, "k%3Dv"),
    ],
)
def test_encode_path_styles(value: Any, style: str, explode: bool, expected: str) -> None:
    assert (
        encode_param_value(value, style=style, explode=explode, location="path") == expected
    )


def test_encode_path_rejects_non_simple_style() -> None:
    with pytest.raises(ValueError, match="unsupported path style"):
        encode_param_value("x", style="label", explode=False, location="path")


def test_encode_header_list_and_rejects_style() -> None:
    assert encode_param_value(["a", "b"], style="simple", explode=False, location="header") == (
        "a,b"
    )
    with pytest.raises(ValueError, match="unsupported header style"):
        encode_param_value("x", style="form", explode=False, location="header")


def test_encode_query_dict_explode_and_no_explode() -> None:
    exploded = encode_param_value({"a": 1, "b": 2}, style="form", explode=True, location="query")
    assert exploded == [("a", "1"), ("b", "2")]
    joined = encode_param_value({"a": 1, "b": 2}, style="form", explode=False, location="query")
    assert joined == "a,1,b,2"


def test_encode_query_rejects_non_form_style() -> None:
    with pytest.raises(ValueError, match="unsupported query style"):
        encode_param_value("x", style="deepObject", explode=True, location="query")


def test_split_params_body_and_unknown_location_ignored() -> None:
    class _BodyInput(BaseModel):
        action: Literal["create"] = "create"
        payload: dict[str, str] = Field(
            ...,
            json_schema_extra={
                "nw_in": "body",
                "nw_media_type": "application/json",
            },
        )
        orphan: str = Field(
            "x",
            json_schema_extra={"nw_in": "cookie"},
        )

    path, query, header, body, media = split_params_by_location(
        _BodyInput(payload={"name": "n"})
    )
    assert path == {} and query == {} and header == {}
    assert body == {"name": "n"}
    assert media == "application/json"


def test_encode_request_body_json_form_multipart_and_raw() -> None:
    from node_wire_runtime.rest import _encode_request_body, _looks_base64

    assert _encode_request_body(None, None) == {}
    assert _encode_request_body({"a": 1}, "application/json") == {"json": {"a": 1}}
    assert _encode_request_body({"a": 1}, "application/vnd.api+json") == {"json": {"a": 1}}

    form = _encode_request_body({"a": 1, "b": True}, "application/x-www-form-urlencoded")
    assert form == {"data": {"a": "1", "b": "True"}}
    with pytest.raises(ValueError, match="form-urlencoded"):
        _encode_request_body("not-object", "application/x-www-form-urlencoded")

    padded = ("ABCD" * 15) + "ab=="  # 64 chars, valid padding
    assert _looks_base64(padded) is True
    multi = _encode_request_body(
        {"file": padded, "note": "hi", "raw": b"bytes"},
        "multipart/form-data",
    )
    assert multi["data"] == {"note": "hi"}
    assert len(multi["files"]) == 2
    with pytest.raises(ValueError, match="multipart"):
        _encode_request_body("x", "multipart/form-data")

    assert _encode_request_body(b"abc", "application/octet-stream") == {"content": b"abc"}
    assert _encode_request_body("plain", "application/octet-stream") == {
        "content": b"plain"
    }
    # base64 string for binary media
    b64 = base64.b64encode(b"hi").decode()
    assert _encode_request_body(b64, "application/octet-stream") == {"content": b"hi"}


def test_encode_request_body_model_binary_and_fallback() -> None:
    from node_wire_runtime.rest import _encode_request_body

    class _Bin(BaseModel):
        data: str

    encoded = base64.b64encode(b"payload").decode()
    out = _encode_request_body(_Bin(data=encoded), "application/octet-stream")
    assert out == {"content": b"payload"}

    class _Multi(BaseModel):
        a: str = "1"
        b: str = "2"

    out2 = _encode_request_body(_Multi(), "application/octet-stream")
    assert isinstance(out2["content"], bytes)

    assert _encode_request_body(123, "application/octet-stream") == {"content": b"123"}


def test_resolve_base_url_override_and_missing() -> None:
    class _NoDefault(RestConnector):
        connector_id = "no_default"
        default_base_url = ""
        output_model = RestResponseOutput

        @nw_action("noop")
        async def noop(self, params: _ListInput, *, trace_id: str) -> RestResponseOutput:
            return RestResponseOutput(status_code=204)

    with pytest.raises(RuntimeError, match="no base_url"):
        _NoDefault().resolve_base_url()

    assert (
        _NoDefault(base_url="https://override.example.com/").resolve_base_url()
        == "https://override.example.com"
    )
    assert _DemoConnector().resolve_base_url() == "https://api.example.com"


@pytest.mark.asyncio
async def test_execute_rest_typed_output_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Out(BaseModel):
        ok: bool

    class _OkResponse:
        status_code = 200
        content = b'{"ok": true}'
        headers = {"content-type": "application/json"}
        text = '{"ok": true}'

        def json(self) -> dict:
            return {"ok": True}

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _OkResponse:
            return _OkResponse()

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination",
        AsyncMock(return_value=None),
    )
    connector = _DemoConnector(base_url="https://api.example.com")
    result = await connector.execute_rest(
        "GET",
        "users/{username}/things",  # missing leading slash → normalized
        _ListInput(username="octocat"),
        output_model=_Out,
        trace_id="t",
        auth=False,
    )
    assert result == _Out(ok=True)


@pytest.mark.asyncio
async def test_execute_rest_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ErrResponse:
        status_code = 404
        content = b"missing"
        headers: dict[str, str] = {}
        text = "missing"

        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "404",
                request=MagicMock(),
                response=MagicMock(status_code=404),
            )

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _ErrResponse:
            return _ErrResponse()

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination",
        AsyncMock(return_value=None),
    )
    connector = _DemoConnector(base_url="https://api.example.com")
    with pytest.raises(httpx.HTTPStatusError):
        await connector.execute_rest(
            "GET",
            "/users/{username}/things",
            _ListInput(username="octocat"),
            output_model=RestResponseOutput,
            trace_id="t",
            auth=False,
        )


# ---------------------------------------------------------------------------
# http_safety SSRF helpers
# ---------------------------------------------------------------------------


def test_is_blocked_ip_ranges() -> None:
    import ipaddress

    from node_wire_runtime.http_safety import is_blocked_ip

    assert is_blocked_ip(ipaddress.ip_address("127.0.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("10.0.0.1")) is True
    assert is_blocked_ip(ipaddress.ip_address("169.254.169.254")) is True
    assert is_blocked_ip(ipaddress.ip_address("8.8.8.8")) is False
    # IPv4-mapped IPv6 loopback
    assert is_blocked_ip(ipaddress.ip_address("::ffff:127.0.0.1")) is True


def test_sanitize_url_ipv6_and_invalid() -> None:
    from node_wire_runtime.http_safety import sanitize_url_for_log

    cleaned = sanitize_url_for_log("https://[2001:db8::1]/path?token=secret")
    assert "token" not in cleaned
    assert "2001:db8::1" in cleaned or "[2001:db8::1]" in cleaned


@pytest.mark.asyncio
async def test_assert_safe_destination_blocks_localhost_and_literal() -> None:
    from node_wire_runtime.http_safety import SsrfBlockedError, assert_safe_destination

    with pytest.raises(SsrfBlockedError, match="missing"):
        await assert_safe_destination("http:///nohost")
    with pytest.raises(SsrfBlockedError, match="blocked"):
        await assert_safe_destination("http://localhost/x")
    with pytest.raises(SsrfBlockedError, match="blocked"):
        await assert_safe_destination("http://127.0.0.1/x")


@pytest.mark.asyncio
async def test_assert_safe_destination_resolves_public_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from node_wire_runtime.http_safety import assert_safe_destination

    async def fake_getaddrinfo(host: str, port: int, **kwargs: Any) -> list:
        return [(None, None, None, None, ("93.184.216.34", port))]

    monkeypatch.setattr(
        "node_wire_runtime.http_safety.asyncio.get_event_loop",
        lambda: MagicMock(getaddrinfo=AsyncMock(side_effect=fake_getaddrinfo)),
    )
    monkeypatch.delenv("NW_REST_ALLOWED_HOSTS", raising=False)
    await assert_safe_destination("https://example.com/ok")
