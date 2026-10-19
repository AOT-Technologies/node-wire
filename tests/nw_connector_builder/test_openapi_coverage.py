# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""OpenAPI conformance: each version's fixture builds, and the generated connector sends
exactly what the spec describes.

The fixtures under ``fixtures/coverage/`` hold one operation per construct; the field-by-field
coverage report is ``docs/cli/openapi-coverage.md``. Each test builds a fixture through the
real pipeline, imports the generated connector, and checks the HTTP request it makes.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from node_wire_runtime.auth import StaticTokenAuthProvider
from node_wire_runtime.rest import RestConnector
from node_wire_runtime.secrets import SecretProvider
from nw_connector_builder.pipeline import run_build

FIXTURES = Path(__file__).parent / "fixtures" / "coverage"


class _Secrets(SecretProvider):
    def get_secret(self, key: str) -> str:
        return {"TOKEN": "tok", "KEY": "k3y"}[key]


def _bearer() -> StaticTokenAuthProvider:
    return StaticTokenAuthProvider(secret_provider=_Secrets(), secret_key="TOKEN")


def _header_key(name: str) -> StaticTokenAuthProvider:
    return StaticTokenAuthProvider(
        secret_provider=_Secrets(), secret_key="KEY", header_name=name, prefix=""
    )


class Built:
    """A fixture built into a scratch node-wire root, with its generated modules imported."""

    def __init__(self, root: Path, connector_id: str) -> None:
        self.root = root
        self.report = json.loads(
            (root / "packages" / "connectors" / connector_id / "report.json").read_text()
        )
        sys.path.insert(0, str(root / "src"))
        try:
            self.schema = importlib.import_module(f"node_wire_{connector_id}.schema")
            self.logic = importlib.import_module(f"node_wire_{connector_id}.logic")
        finally:
            sys.path.remove(str(root / "src"))
        (self.cls,) = [
            v
            for v in vars(self.logic).values()
            if isinstance(v, type) and issubclass(v, RestConnector) and v is not RestConnector
        ]

    def model(self, action: str) -> type:
        return getattr(self.schema, "".join(p.title() for p in action.split("_")) + "Input")

    def call(self, monkeypatch: pytest.MonkeyPatch, action: str, connector=None, **args: Any):
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
        connector = connector or self.cls(auth_provider=_bearer())
        params = self.model(action)(**args)
        asyncio.run(getattr(connector, action)(params, trace_id="t"))
        return captured[0]


def _build(tmp_path_factory: pytest.TempPathFactory, fixture: str) -> Built:
    connector_id = f"cov_{fixture}"
    root = tmp_path_factory.mktemp(fixture) / "nw"
    for d in ("src", "packages/connectors", "config", "nw-mcp-builder"):
        (root / d).mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "sample.env").write_text("NW_ALLOWED_CONNECTORS=\n", encoding="utf-8")
    code = run_build(
        spec=str(FIXTURES / f"{fixture}.yaml"),
        connector_id=connector_id,
        node_wire_root=root,
        no_mcp=True,
        wire=True,
        report_path=root / "abort.json",
    )
    assert code == 0
    return Built(root, connector_id)


@pytest.fixture(scope="module")
def oas30(tmp_path_factory: pytest.TempPathFactory) -> Built:
    return _build(tmp_path_factory, "oas30")


@pytest.fixture(scope="module")
def oas31(tmp_path_factory: pytest.TempPathFactory) -> Built:
    return _build(tmp_path_factory, "oas31")


@pytest.fixture(scope="module")
def oas32(tmp_path_factory: pytest.TempPathFactory) -> Built:
    return _build(tmp_path_factory, "oas32")


@pytest.fixture(scope="module")
def swagger20(tmp_path_factory: pytest.TempPathFactory) -> Built:
    return _build(tmp_path_factory, "swagger20")


def _query(req: dict[str, Any]) -> str:
    return req["url"].split("?", 1)[1] if "?" in req["url"] else ""


# --- OpenAPI 3.0 -------------------------------------------------------------------


def test_oas30_every_operation_builds_and_inbound_ones_are_noted(oas30: Built) -> None:
    summary = oas30.report["summary"]
    assert (summary["generated"], summary["total_operations"]) == (12, 12)
    notes = " ".join(oas30.report["notes"])
    assert "callbacks on 1 operation(s)" in notes
    assert "GET /limits → https://other.example.com" in notes
    assert "get_item.Accept" in notes


def test_oas30_server_variables_path_overrides_and_booleans(
    oas30: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    fields = oas30.model("get_item").model_fields
    assert "accept" not in fields  # ignored as the spec requires
    assert fields["verbose"].annotation == bool | None  # operation level beat path level
    req = oas30.call(monkeypatch, "get_item", id="a b", verbose=True)
    assert req["url"] == "https://eu.api.example.com/v1/items/a%20b?verbose=true"
    assert req["headers"]["Authorization"] == "Bearer tok"


def test_oas30_every_parameter_style_and_location(
    oas30: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = oas30.call(
        monkeypatch,
        "styles",
        label=["x", "y"],
        matrix=["m1", "m2"],
        tags=["a", "b"],
        words=["c", "d"],
        filter={"k": "v"},
        next="https://n.test/p?q",
        where={"n": 1},
        x_meta={"n": 1},
        session="s",
    )
    assert req["url"].split("?")[0] == "https://eu.api.example.com/v1/styles/.x.y/;matrix=m1,m2"
    query = _query(req)
    assert "tags=a%7Cb" in query and "words=c%20d" in query
    assert "filter[k]=v" in query
    assert "next=https://n.test/p?q" in query  # allowReserved
    assert httpx.URL(req["url"]).params["where"] == '{"n":1}'  # content: application/json
    assert req["headers"]["X-Meta"] == "n=1"
    assert req["headers"]["Cookie"] == "session=s"


def test_oas30_schema_constraints_are_enforced_and_advertised(oas30: Built) -> None:
    model = oas30.model("limits")
    model(page=1, code="ABC")
    for bad in (
        {"page": 0, "code": "ABC"},
        {"page": 1, "code": "AB"},
        {"page": 1, "code": "ABC", "level": 4},
    ):
        with pytest.raises(ValidationError):
            model(**bad)
    with pytest.raises(ValidationError):
        model(page=1, code="ABC", ratio=0)  # exclusiveMinimum: true (3.0 boolean form)
    with pytest.raises(ValidationError):
        model(page=1, code="ABC", ids=[])
    props = model.model_json_schema()["properties"]
    assert props["code"]["pattern"] == "^[A-Z]+$"
    assert props["when"]["format"] == "date-time"
    assert oas30.cls.nw_action_metas()["limits"].deprecated is True


def test_oas30_operation_server_is_used_for_that_operation(
    oas30: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = oas30.call(monkeypatch, "limits", page=2, code="ABC")
    assert req["url"].startswith("https://other.example.com/limits?")


def test_oas30_request_bodies_by_media_type(oas30: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "id" not in oas30.model("post_json").model_fields  # readOnly
    assert oas30.call(monkeypatch, "post_json", name="n")["json"] == {"name": "n"}

    form = oas30.call(monkeypatch, "post_form", flag=True, tags=["a", "b"])
    assert form["data"] == {"flag": "true", "tags": ["a", "b"]}

    png = base64.b64encode(b"\x89PNG").decode()
    multi = oas30.call(monkeypatch, "upload", file=png, note="n")
    assert multi["files"] == [("file", ("file", b"\x89PNG", "image/png"))]
    assert multi["data"] == {"note": "n"}

    text = oas30.call(monkeypatch, "post_text", body="hello")
    assert text["content"] == b"hello" and text["headers"]["Content-Type"] == "text/plain"
    xml = oas30.call(monkeypatch, "post_xml", body="<item/>")
    assert xml["content"] == b"<item/>"
    assert xml["headers"]["Content-Type"] == "application/xml"
    raw = oas30.call(monkeypatch, "put_raw", body=base64.b64encode(b"\x00\x01").decode())
    assert raw["content"] == b"\x00\x01"


def test_oas30_xml_response_get_body_and_cookie_explode(
    oas30: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    from node_wire_runtime.rest import RestResponseOutput

    # A non-JSON success response is returned in the envelope, never parsed as JSON.
    assert oas30.logic.XmlOutOutput is RestResponseOutput or issubclass(
        getattr(oas30.schema, "XmlOutOutput"), RestResponseOutput
    )
    # OAS 3.0 ignores a requestBody on GET.
    assert "ignored" not in oas30.model("xml_out").model_fields
    req = oas30.call(monkeypatch, "xml_out", prefs=["a", "b"])
    assert req["headers"]["Cookie"] == "prefs=a; prefs=b"  # form: explode defaults to true


def test_oas30_and_combined_security_and_anonymous(
    oas30: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = oas30.cls(
        auth_provider=_bearer(), auth_providers={"apiKey": _header_key("X-Api-Key")}
    )
    req = oas30.call(monkeypatch, "needs_both", connector=connector)
    assert req["headers"]["Authorization"] == "Bearer tok"
    assert req["headers"]["X-Api-Key"] == "k3y"
    assert "Authorization" not in oas30.call(monkeypatch, "public_thing")["headers"]


# --- OpenAPI 3.1 -------------------------------------------------------------------


def test_oas31_json_schema_forms(oas31: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "1 webhook(s)" in " ".join(oas31.report["notes"])
    model = oas31.model("list_things")
    with pytest.raises(ValidationError):
        model(kind="gadget")  # const
    with pytest.raises(ValidationError):
        model(kind="widget", size=0)  # exclusiveMinimum: 0 (3.1 number form)
    with pytest.raises(ValidationError):
        model(kind="widget", label="x" * 21)  # $ref target's maxLength
    props = model.model_json_schema()["properties"]
    assert props["scale"]["enum"] == [0.5, 1.5]  # number enum, advertised
    assert "anything" in props  # `schema: true`
    req = oas31.call(monkeypatch, "list_things", connector=oas31.cls(), kind="widget", size=2)
    assert req["url"] == "https://api.example.com/things?kind=widget&size=2"

    png = base64.b64encode(b"\x89PNG").decode()
    token = base64.urlsafe_b64encode(b"\xfb\xff").decode().rstrip("=")
    multi = oas31.call(monkeypatch, "upload31", connector=oas31.cls(), file=png, token=token)
    assert multi["files"] == [
        ("file", ("file", b"\x89PNG")),  # contentEncoding: base64
        ("token", ("token", b"\xfb\xff")),  # contentEncoding: base64url, unpadded
    ]


# --- OpenAPI 3.2 -------------------------------------------------------------------


def test_oas32_query_method_additional_operations_and_querystring(
    oas32: Built, monkeypatch: pytest.MonkeyPatch
) -> None:
    search = oas32.call(monkeypatch, "search", q="cats")
    assert search["method"] == "QUERY" and search["json"] == {"q": "cats"}
    assert oas32.call(monkeypatch, "purge_search")["method"] == "PURGE"
    filtered = oas32.call(monkeypatch, "filtered", q={"a": "1", "b": "x y"})
    assert filtered["url"] == "https://api.example.com/filtered?a=1&b=x%20y"
    assert oas32.report["auth"]["provider"] == "static_token"  # deviceAuthorization flow
    files = oas32.call(monkeypatch, "get_file", path="docs/a b.md", sid="a%b")
    assert files["url"] == "https://api.example.com/files/docs/a%20b.md"  # allowReserved path
    assert files["headers"]["Cookie"] == "sid=a%b"  # style: cookie, sent as-is


# --- Swagger 2.0 -------------------------------------------------------------------


def test_swagger20_conversion(swagger20: Built, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = " ".join(swagger20.report["notes"])
    assert "get_things.tabs (unsupported collectionFormat tsv" in notes
    assert swagger20.report["summary"]["generated"] == 5
    # path-level `fields` overridden by the operation's (one argument, the array type)
    fields = [f for f in swagger20.model("get_pet").model_fields if f.startswith("fields")]
    assert fields == ["fields"]
    assert swagger20.model("get_pet").model_fields["fields"].annotation == list[list[str]] | None

    def key_auth() -> RestConnector:
        from node_wire_runtime.auth import ApiKeyQueryAuthProvider

        return swagger20.cls(
            auth_provider=ApiKeyQueryAuthProvider(
                secret_provider=_Secrets(), secret_key="KEY", name="api_key"
            )
        )

    req = swagger20.call(
        monkeypatch, "get_things", connector=key_auth(), ids=["a", "b"], words=["c", "d"], limit=2
    )
    # schemes [http, https] → https; csv in path → simple; ssv → spaceDelimited;
    # the global #/parameters/Limit is inlined.
    assert req["url"].split("?")[0] == "https://api.example.com/v2/things/a,b"
    assert httpx.URL(req["url"]).params["words"] == "c d"
    assert httpx.URL(req["url"]).params["limit"] == "2"
    with pytest.raises(ValidationError):
        swagger20.model("get_things")(ids=["a"], limit=0)  # Limit's minimum: 1

    # A global body parameter, flattened into the JSON body.
    assert swagger20.call(monkeypatch, "create_thing", connector=key_auth(), name="x")["json"] == {
        "name": "x"
    }
    # consumes: multipart/form-data without a file field is still multipart.
    form = swagger20.call(monkeypatch, "send_form", connector=key_auth(), note="n")
    assert form["files"] == [("note", (None, "n"))]
