#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""``headers:``: fixed headers a REST connector sends on every request."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, Field

from bindings.factory import ConnectorFactory
from node_wire_runtime import nw_action
from node_wire_runtime.auth import StaticTokenAuthProvider
from node_wire_runtime.config_store import ConfigStoreError, ConnectorConfigStore
from node_wire_runtime.rest import RestConnector, RestResponseOutput, validate_static_headers
from node_wire_runtime.secrets import SecretProvider

_CONNECTOR = "hdr_demo_rest"


class _Secrets(SecretProvider):
    def __init__(self, data: dict[str, str]) -> None:
        self.data = data

    def get_secret(self, key: str) -> str:
        return self.data[key]


class _PingInput(BaseModel):
    action: Literal["ping"] = "ping"
    client_id: str | None = Field(
        None,
        alias="Client-Id",
        json_schema_extra={
            "nw_in": "header",
            "nw_wire_name": "Client-Id",
            "nw_style": "simple",
            "nw_explode": False,
        },
    )


class HeaderDemoConnector(RestConnector):
    connector_id = _CONNECTOR
    default_base_url = "https://api.example.com"
    output_model = RestResponseOutput

    @nw_action("ping")
    async def ping(self, params: _PingInput, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET", "/ping", params, output_model=RestResponseOutput, trace_id=trace_id
        )


def _install_http(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class _FakeResponse:
        status_code = 200
        content = b"{}"
        headers = {"content-type": "application/json"}
        text = "{}"

        def json(self) -> dict:
            return {}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _FakeResponse:
            captured.append(kwargs)
            return _FakeResponse()

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _FakeClient)
    monkeypatch.setattr("node_wire_runtime.rest.assert_safe_destination", _allow)
    return captured


def _ping(connector: RestConnector, **params: Any) -> None:
    asyncio.run(connector.ping(_PingInput(**params), trace_id="t"))


def test_configured_headers_are_sent_and_auth_still_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_http(monkeypatch)
    secrets = _Secrets({"DEMO_TOKEN": "tok"})
    connector = HeaderDemoConnector(
        secret_provider=secrets,
        auth_provider=StaticTokenAuthProvider(secret_provider=secrets, secret_key="DEMO_TOKEN"),
        headers={"Client-Id": "abc123", "X-Api-Version": "2022-11-28"},
    )
    _ping(connector)
    sent = captured[0]["headers"]
    assert sent["Client-Id"] == "abc123"
    assert sent["X-Api-Version"] == "2022-11-28"
    assert sent["Authorization"] == "Bearer tok"


def test_configured_header_beats_a_caller_supplied_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_http(monkeypatch)
    connector = HeaderDemoConnector(secret_provider=_Secrets({}), headers={"Client-Id": "ops"})
    _ping(connector, **{"Client-Id": "caller"})
    assert captured[0]["headers"]["Client-Id"] == "ops"


def test_unset_env_placeholder_fails_instead_of_being_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_http(monkeypatch)
    connector = HeaderDemoConnector(
        secret_provider=_Secrets({}), headers={"Client-Id": "${TWITCH_CLIENT_ID}"}
    )
    with pytest.raises(RuntimeError, match=r"still holds \$\{TWITCH_CLIENT_ID\}: set TWITCH"):
        _ping(connector)
    assert captured == []


@pytest.mark.parametrize(
    ("headers", "match"),
    [
        (["Client-Id"], "mapping"),
        ({"Bad Name": "x"}, "valid HTTP field name"),
        ({"Authorization": "Bearer x"}, "cannot be set"),
        ({"content-type": "text/plain"}, "cannot be set"),
        ({"X-Split": "a\r\nX-Injected: b"}, "single-line"),
        ({"X-Number": 5}, "single-line"),
    ],
)
def test_invalid_headers_are_refused(headers: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_static_headers(headers)


def _doc(**extra: Any) -> dict[str, Any]:
    return {"name": "live", "default": True, "auth": {"provider": "none"}, **extra}


@pytest.mark.parametrize(
    "doc",
    [
        _doc(headers={"Authorization": "Bearer smuggled"}),
        _doc(config={"headers": {"X-Split": "a\r\nX-Injected: b"}}),
        _doc(headers="Client-Id: x"),
    ],
)
def test_config_documents_with_invalid_headers_are_refused(doc: dict) -> None:
    with pytest.raises(ConfigStoreError):
        ConnectorConfigStore().init({"acme": {_CONNECTOR: [doc]}})


def test_config_documents_accept_valid_headers() -> None:
    ConnectorConfigStore().init({"acme": {_CONNECTOR: [_doc(headers={"Client-Id": "abc123"})]}})


def test_connectors_yaml_headers_are_env_substituted_and_reach_the_connector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _install_http(monkeypatch)
    config = tmp_path / "connectors.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "connectors": {
                    _CONNECTOR: {
                        "enabled": True,
                        "exposed_via": ["rest"],
                        "auth": {"provider": "none"},
                        "headers": {"Client-Id": "${DEMO_CLIENT_ID}"},
                    }
                }
            }
        )
    )
    monkeypatch.setenv("DEMO_CLIENT_ID", "from-env")
    monkeypatch.setenv("NW_MULTITENANCY_ENABLED", "false")
    factory = ConnectorFactory(config)
    factory.load()

    async def _run() -> None:
        connector = await factory.get(_CONNECTOR)
        await connector.ping(_PingInput(), trace_id="t")

    asyncio.run(_run())
    assert captured[0]["headers"]["Client-Id"] == "from-env"
