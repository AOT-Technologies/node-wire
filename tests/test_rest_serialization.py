#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""OpenAPI parameter and body serialization on the wire (``RestConnector.execute_rest``)."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from node_wire_runtime import nw_action
from node_wire_runtime.auth import ApiKeyQueryAuthProvider, StaticTokenAuthProvider
from node_wire_runtime.rest import RestConnector, RestResponseOutput, _encode_request_body
from node_wire_runtime.secrets import SecretProvider


class _Secrets(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"TOKEN": "tok", "KEY": "k3y"}[key]


def _extra(loc: str, wire: str, **more: Any) -> dict[str, Any]:
    return {"nw_in": loc, "nw_wire_name": wire, "nw_style": None, "nw_explode": None, **more}


class _Input(BaseModel):
    action: Literal["call"] = "call"
    item: list[str] | None = Field(
        None, json_schema_extra=_extra("path", "item", nw_style="matrix")
    )
    session: str | None = Field(None, json_schema_extra=_extra("cookie", "session"))
    prefs: list[str] | None = Field(None, json_schema_extra=_extra("cookie", "prefs"))
    filter_: dict[str, Any] | None = Field(
        None, json_schema_extra=_extra("query", "filter", nw_style="deepObject", nw_explode=True)
    )
    redirect: str | None = Field(
        None, json_schema_extra=_extra("query", "redirect", nw_allow_reserved=True)
    )
    plain: str | None = Field(None, json_schema_extra=_extra("query", "plain"))
    where: dict[str, Any] | None = Field(
        None, json_schema_extra=_extra("query", "where", nw_media_type="application/json")
    )
    flag: bool | None = Field(None, json_schema_extra=_extra("header", "X-Flag"))
    qs: dict[str, Any] | None = Field(
        None,
        json_schema_extra=_extra(
            "querystring", "qs", nw_media_type="application/x-www-form-urlencoded"
        ),
    )


class _Demo(RestConnector):
    connector_id = "ser_demo"
    default_base_url = "https://api.example.com"
    output_model = RestResponseOutput

    @nw_action("call")
    async def call(self, params: _Input, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET", "/things/{item}", params, output_model=RestResponseOutput, trace_id=trace_id
        )


def _install(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class _Resp:
        status_code = 200
        content = b"{}"
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self) -> dict:
            return {}

    class _Client:
        def __init__(self, *a: Any, **k: Any) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _Resp:
            captured.append(kwargs)
            return _Resp()

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _Client)
    monkeypatch.setattr("node_wire_runtime.rest.assert_safe_destination", _allow)
    return captured


def _call(monkeypatch: pytest.MonkeyPatch, **params: Any) -> dict[str, Any]:
    captured = _install(monkeypatch)
    asyncio.run(_Demo().call(_Input(**params), trace_id="t"))
    return captured[0]


def test_matrix_path_cookies_and_boolean_header(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _call(monkeypatch, item=["a", "b"], session="s 1", prefs=["x", "y"], flag=False)
    assert req["url"] == "https://api.example.com/things/;item=a,b"
    # form cookies: values percent-encoded, arrays exploded by default
    assert req["headers"]["Cookie"] == "session=s%201; prefs=x; prefs=y"
    assert req["headers"]["X-Flag"] == "false"


def test_query_styles_reserved_and_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _call(
        monkeypatch,
        item=["i"],
        filter_={"color": "red"},
        redirect="https://x.test/a?b",
        plain="https://x.test/a",
        where={"n": 1},
    )
    query = req["url"].split("?", 1)[1]
    assert "filter[color]=red" in query
    assert "redirect=https://x.test/a?b" in query  # allowReserved: sent as-is
    assert "plain=https%3A%2F%2Fx.test%2Fa" in query
    assert httpx.URL(req["url"]).params["where"] == '{"n":1}'


def test_querystring_parameter_is_the_whole_query(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _call(monkeypatch, item=["i"], qs={"a": 1, "b": "x y"})
    assert req["url"].endswith("?a=1&b=x%20y")


def test_and_combined_auth_sends_every_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install(monkeypatch)
    secrets = _Secrets()
    connector = _Demo(
        secret_provider=secrets,
        auth_providers={
            "bearer": StaticTokenAuthProvider(secret_provider=secrets, secret_key="TOKEN"),
            "key": ApiKeyQueryAuthProvider(secret_provider=secrets, secret_key="KEY", name="key"),
        },
    )

    async def _run() -> None:
        await connector.execute_rest(
            "GET",
            "/things/{item}",
            _Input(item=["i"]),
            output_model=RestResponseOutput,
            trace_id="t",
            auth_scheme=("bearer", "key"),
        )

    asyncio.run(_run())
    assert captured[0]["headers"]["Authorization"] == "Bearer tok"
    assert httpx.URL(captured[0]["url"]).params["key"] == "k3y"


def test_text_and_xml_bodies_are_sent_as_text() -> None:
    assert _encode_request_body("# Hello", "text/markdown") == {"content": b"# Hello"}
    xml = "<a>test</a>"
    assert _encode_request_body(xml, "application/xml") == {"content": xml.encode()}
    # "test" is valid base64; text bodies must not be decoded.
    assert _encode_request_body("test", "text/plain") == {"content": b"test"}


def test_binary_formats_are_decoded() -> None:
    raw = base64.b64encode(b"\x00\x01").decode()
    assert _encode_request_body(raw, "text/plain", body_format="binary") == {"content": b"\x00\x01"}
    multi = _encode_request_body(
        {"file": raw, "name": "n"}, "multipart/form-data", part_formats={"file": "binary"}
    )
    assert multi["files"] == [("file", ("file", b"\x00\x01"))]
    assert multi["data"] == {"name": "n"}
