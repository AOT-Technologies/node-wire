#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""A playground scenario for a connector that is not loaded answers with a clear 503, not a bare 500."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from playground import scenarios

_NOT_LOADED = (
    "Connector 'http_generic' has a config but is not registered "
    "(filtered by NW_ALLOWED_CONNECTORS or not installed)"
)


class _Factory:
    def is_exposed(self, connector_id: str, protocol: str) -> bool:
        return True

    async def get(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError(_NOT_LOADED)


async def test_a_connector_that_is_not_loaded_is_a_503_that_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scenarios, "get_playground_factory", lambda: _Factory())
    monkeypatch.setattr("bindings.rest_api.auth.get_rest_caller_identity", lambda request: None)
    with pytest.raises(HTTPException) as caught:
        await scenarios.resolve_connector(Request({"type": "http", "headers": []}), "http_generic")
    assert caught.value.status_code == 503
    assert "NW_ALLOWED_CONNECTORS" in str(caught.value.detail)
