#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""``execute_rest(envelope_ok_field=...)`` — 2xx bodies that report their own failure.

Slack-style APIs answer ``200`` with ``{"ok": false, "error": "..."}``; without
this check the call looks successful to every layer above the executor.
"""

from __future__ import annotations

from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from node_wire_runtime import nw_action
from node_wire_runtime.rest import RestConnector, RestEnvelopeError, RestResponseOutput
from node_wire_runtime.secrets import SecretProvider


class _NoSecrets(SecretProvider):
    def get_secret(self, key: str) -> str:  # pragma: no cover - never called
        raise KeyError(key)


class _Input(BaseModel):
    action: Literal["ping"] = "ping"


class _TypedOutput(BaseModel):
    ok: bool
    error: str | None = None


class _EnvelopeConnector(RestConnector):
    connector_id = "envelope_demo"
    default_base_url = "https://api.example.com"
    output_model = RestResponseOutput

    @nw_action("ping", requires_auth=False)
    async def ping(self, params: _Input, *, trace_id: str) -> _TypedOutput:
        return await self.execute_rest(
            "GET",
            "/ping",
            params,
            output_model=_TypedOutput,
            trace_id=trace_id,
            auth=False,
            envelope_ok_field="ok",
        )

    @nw_action("ping_untyped", requires_auth=False)
    async def ping_untyped(self, params: _Input, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET",
            "/ping",
            params,
            output_model=RestResponseOutput,
            trace_id=trace_id,
            auth=False,
            envelope_ok_field="ok",
        )

    @nw_action("ping_unchecked", requires_auth=False)
    async def ping_unchecked(self, params: _Input, *, trace_id: str) -> RestResponseOutput:
        return await self.execute_rest(
            "GET", "/ping", params, output_model=RestResponseOutput, trace_id=trace_id, auth=False
        )


def _install_response(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    import json as _json

    text = _json.dumps(payload)

    class _Response:
        status_code = 200
        content = text.encode()
        headers = {"content-type": "application/json"}

        def json(self) -> Any:
            return payload

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr("node_wire_runtime.rest.httpx.AsyncClient", _Client)
    monkeypatch.setattr(
        "node_wire_runtime.rest.assert_safe_destination", AsyncMock(return_value=None)
    )


def _connector() -> _EnvelopeConnector:
    return _EnvelopeConnector(secret_provider=_NoSecrets())


@pytest.mark.asyncio
async def test_ok_false_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_response(monkeypatch, {"ok": False, "error": "invalid_auth"})
    with pytest.raises(RestEnvelopeError) as exc:
        await _connector().ping(_Input(), trace_id="t")
    assert exc.value.field == "ok"
    assert "invalid_auth" in str(exc.value)


@pytest.mark.asyncio
async def test_ok_true_returns_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_response(monkeypatch, {"ok": True})
    out = await _connector().ping(_Input(), trace_id="t")
    assert out.ok is True


@pytest.mark.asyncio
async def test_missing_flag_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence says nothing; only a flag the API actually sent to false is a failure."""
    _install_response(monkeypatch, {"error": None, "ok": None})
    out = await _connector().ping_untyped(_Input(), trace_id="t")
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_untyped_envelope_output_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_response(monkeypatch, {"ok": False})
    with pytest.raises(RestEnvelopeError):
        await _connector().ping_untyped(_Input(), trace_id="t")


@pytest.mark.asyncio
async def test_check_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every existing connector calls execute_rest without the kwarg — unchanged."""
    _install_response(monkeypatch, {"ok": False, "error": "nope"})
    out = await _connector().ping_unchecked(_Input(), trace_id="t")
    assert out.body == {"ok": False, "error": "nope"}


@pytest.mark.asyncio
async def test_non_dict_body_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_response(monkeypatch, [1, 2, 3])
    out = await _connector().ping_untyped(_Input(), trace_id="t")
    assert out.body == [1, 2, 3]
