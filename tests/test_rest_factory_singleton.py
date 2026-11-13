#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""The REST app's lazy ConnectorFactory is published only once it has loaded."""

from __future__ import annotations

import pytest

import bindings.rest_api.app as rest_app
from bindings.factory import ConnectorFactory


def test_failed_load_does_not_publish_a_half_built_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rest_app, "_factory", None)
    real_load = ConnectorFactory.load
    attempts = {"n": 0}

    def flaky_load(self: ConnectorFactory, *args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("connectors.yaml unreadable")
        return real_load(self, *args, **kwargs)

    monkeypatch.setattr(ConnectorFactory, "load", flaky_load)
    with pytest.raises(RuntimeError):
        rest_app.get_factory()
    assert rest_app._factory is None

    factory = rest_app.get_factory()
    assert attempts["n"] == 2
    assert rest_app.get_factory() is factory
