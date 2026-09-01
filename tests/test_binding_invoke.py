#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bindings.invoke import ConnectorNotExposed, invoke
from node_wire_runtime import ConnectorResponse
from node_wire_runtime.config_store import ConfigNotFoundError


@pytest.fixture()
def fake_connector() -> MagicMock:
    conn = MagicMock()
    conn.run = AsyncMock(
        return_value=ConnectorResponse(success=True, data={"ok": True}, trace_id="t1")
    )
    return conn


@pytest.fixture()
def fake_factory(fake_connector: MagicMock) -> MagicMock:
    factory = MagicMock()
    factory.is_exposed = MagicMock(return_value=True)
    factory.get = AsyncMock(return_value=fake_connector)
    return factory


@pytest.mark.asyncio
async def test_invoke_not_exposed(fake_factory: MagicMock, fake_connector: MagicMock) -> None:
    fake_factory.is_exposed.return_value = False

    with pytest.raises(ConnectorNotExposed):
        await invoke(
            fake_factory,
            connector_id="slack",
            action="post_message",
            payload={},
            protocol="rest",
            tenant_id="__default__",
        )

    fake_factory.get.assert_not_called()
    fake_connector.run.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_config_not_found(fake_factory: MagicMock, fake_connector: MagicMock) -> None:
    fake_factory.get.side_effect = ConfigNotFoundError("missing")

    with pytest.raises(ConfigNotFoundError):
        await invoke(
            fake_factory,
            connector_id="slack",
            action="post_message",
            payload={},
            protocol="mcp",
            tenant_id="acme",
            config_name="nope",
        )

    fake_connector.run.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_action_mismatch(fake_factory: MagicMock, fake_connector: MagicMock) -> None:
    with pytest.raises(ValueError, match="does not match"):
        await invoke(
            fake_factory,
            connector_id="slack",
            action="post_message",
            payload={"action": "other"},
            protocol="grpc",
            tenant_id="__default__",
        )

    fake_connector.run.assert_not_called()


@pytest.mark.asyncio
async def test_invoke_happy_path(fake_factory: MagicMock, fake_connector: MagicMock) -> None:
    resp = await invoke(
        fake_factory,
        connector_id="slack",
        action="post_message",
        payload={"text": "hi"},
        protocol="rest",
        tenant_id="acme",
        config_name="main",
        principal="user-1",
        scopes=("read",),
    )

    assert resp.success is True
    fake_factory.get.assert_awaited_once_with(
        "slack",
        tenant_id="acme",
        config_name="main",
    )
    fake_connector.run.assert_awaited_once()
    kwargs = fake_connector.run.await_args.kwargs
    assert kwargs["principal"] == "user-1"
    assert kwargs["tenant_id"] == "acme"
    assert kwargs["scopes"] == ("read",)
    payload = fake_connector.run.await_args.args[0]
    assert payload["action"] == "post_message"
    assert payload["text"] == "hi"


@pytest.mark.asyncio
async def test_invoke_get_does_not_pass_action(fake_factory: MagicMock) -> None:
    await invoke(
        fake_factory,
        connector_id="slack",
        action="post_message",
        payload={},
        protocol="rest",
        tenant_id="__default__",
    )

    _, kwargs = fake_factory.get.call_args
    assert "action" not in kwargs
