#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""
Proves SdkActionSpec generalizes beyond discovery-style clients (googleapiclient):

- a flat-class SDK shape (e.g. stripe.Charge.create(...), no .execute() step)
- a natively-async SDK method (no thread offload needed)

Discovery-style specs (Google Drive) still resolve identically without
opting into any of the new fields — see test_google_drive_action_spec.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

import warnings

from node_wire_runtime.sdk_action_spec import (
    SdkActionSpec,
    execute_spec_async,
    execute_spec_sync,
    navigate_resource,
)


class _ChargeInput(BaseModel):
    amount: int
    currency: str


def test_flat_class_sdk_without_execute_step():
    """Stripe-shaped SDK: resource_segments=("Charge",) is an attribute, not a
    call, and the method's return value is the result directly."""
    client = MagicMock()
    client.Charge.create.return_value = {"id": "ch_123", "amount": 500}

    spec = SdkActionSpec(
        resource_segments=("Charge",),
        method_name="create",
        kwargs_from_model={"amount": "amount", "currency": "currency"},
        call_segments=False,
        invoke=lambda method, kwargs: method(**kwargs),
    )

    result = execute_spec_sync(client, spec, _ChargeInput(amount=500, currency="usd"))

    client.Charge.create.assert_called_once_with(amount=500, currency="usd")
    assert result == {"id": "ch_123", "amount": 500}


def test_resolve_method_full_override_for_argument_taking_segments():
    """Some SDKs need an argument mid-navigation (e.g. PyGithub's
    get_repo(name)) — resolve_method fully replaces segment-walking."""
    client = MagicMock()
    repo = client.get_repo.return_value
    repo.get_issues.return_value = ["issue-1"]

    spec = SdkActionSpec(
        resource_segments=(),
        method_name="get_issues",
        resolve_method=lambda spec, client: client.get_repo("acme/widgets").get_issues,
        invoke=lambda method, kwargs: method(**kwargs),
    )

    result = execute_spec_sync(client, spec, _ChargeInput(amount=1, currency="usd"))

    client.get_repo.assert_called_once_with("acme/widgets")
    assert result == ["issue-1"]


@pytest.mark.asyncio
async def test_natively_async_sdk_method_no_thread_offload():
    """A coroutine-function SDK method: invoke returns a coroutine, and
    execute_spec_async awaits it directly on the running loop."""

    calls: dict[str, Any] = {}

    class _AsyncClient:
        async def create(self, **kwargs):
            calls.update(kwargs)
            return {"ok": True}

    spec = SdkActionSpec(
        resource_segments=(),
        method_name="create",
        kwargs_from_model={"amount": "amount"},
        resolve_method=lambda spec, client: client.create,
        invoke=lambda method, kwargs: method(**kwargs),
    )

    result = await execute_spec_async(_AsyncClient(), spec, _ChargeInput(amount=42, currency="usd"))

    assert calls == {"amount": 42}
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_post_process_runs_on_async_path():
    """post_process must run after the coroutine is awaited on the async path."""

    class _AsyncClient:
        async def create(self, **kwargs):
            return {"raw": kwargs["amount"]}

    spec = SdkActionSpec(
        resource_segments=(),
        method_name="create",
        kwargs_from_model={"amount": "amount"},
        resolve_method=lambda spec, client: client.create,
        invoke=lambda method, kwargs: method(**kwargs),
        post_process=lambda result, model: {"doubled": result["raw"] * 2},
    )

    result = await execute_spec_async(_AsyncClient(), spec, _ChargeInput(amount=21, currency="usd"))

    assert result == {"doubled": 42}


def test_execute_spec_sync_raises_on_coroutine_invoke():
    """An async invoke on the sync path is a caller error: raise rather than
    silently return an un-awaited coroutine (also covers execute_spec_in_thread,
    which wraps execute_spec_sync)."""

    async def _coro():
        return {"ok": True}

    spec = SdkActionSpec(
        resource_segments=(),
        method_name="create",
        resolve_method=lambda spec, client: (lambda **kw: _coro()),
        invoke=lambda method, kwargs: method(**kwargs),
    )

    with pytest.raises(RuntimeError, match="coroutine"):
        execute_spec_sync(MagicMock(), spec, _ChargeInput(amount=1, currency="usd"))


def test_call_segments_false_without_invoke_warns():
    """Flat-class shape (call_segments=False) with no invoke override still hits
    default_invoke's `.execute()` step — flag the likely-wrong combination at
    construction time."""
    with pytest.warns(UserWarning, match=r"\.execute\(\)"):
        SdkActionSpec(
            resource_segments=("Charge",),
            method_name="create",
            call_segments=False,
        )


def test_call_segments_false_with_invoke_does_not_warn():
    """Providing the invoke override is the correct flat-class shape — no warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        SdkActionSpec(
            resource_segments=("Charge",),
            method_name="create",
            call_segments=False,
            invoke=lambda method, kwargs: method(**kwargs),
        )


def test_navigate_resource_is_deprecated():
    """navigate_resource is unused by the execute path and kept only for
    backward compatibility; it warns but still walks the segments."""
    client = MagicMock()
    with pytest.warns(DeprecationWarning):
        result = navigate_resource(client, ("files",))

    client.files.assert_called_once_with()
    assert result == client.files.return_value
