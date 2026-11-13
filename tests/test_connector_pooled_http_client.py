#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Hand-written connectors reuse the runtime's pooled httpx client instead of one per call."""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "node_wire_fhir_cerner.logic",
    "node_wire_fhir_epic.logic",
    "node_wire_salesforce.logic",
    "node_wire_slack.logic",
    "node_wire_http_generic.logic",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("module_name", MODULES)
async def test_http_client_is_pooled_per_loop(module_name: str) -> None:
    module = importlib.import_module(module_name)
    async with module._http_client() as first:
        pass
    async with module._http_client() as second:
        pass
    assert first is second
    assert not first.is_closed  # leaving the block must not close the shared client


@pytest.mark.asyncio
async def test_http_generic_keeps_proxy_env_off() -> None:
    module = importlib.import_module("node_wire_http_generic.logic")
    async with module._http_client(trust_env=False) as no_env:
        pass
    async with module._http_client() as with_env:
        pass
    assert no_env is not with_env
    assert no_env.trust_env is False
    assert no_env.follow_redirects is False
