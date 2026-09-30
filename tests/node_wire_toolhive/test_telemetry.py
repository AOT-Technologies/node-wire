# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""init_telemetry: redaction always, OpenTelemetry only with an OTLP endpoint."""

from __future__ import annotations

import logging
from typing import Iterator, List

import pytest

from node_wire_runtime.log_sanitization import SanitizingLogFilter
from node_wire_toolhive import init_telemetry, telemetry


@pytest.fixture
def root_handler() -> Iterator[logging.Handler]:
    root = logging.getLogger()
    original_filters = list(root.filters)
    handler = logging.NullHandler()
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        for flt in list(root.filters):
            root.removeFilter(flt)
        for flt in original_filters:
            root.addFilter(flt)


@pytest.fixture
def otel_calls(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    calls: List[str] = []
    monkeypatch.setattr(telemetry, "init_observability", lambda app_name: calls.append(app_name))
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_SDK_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    return calls


def test_redaction_is_installed_on_the_root_handlers(
    root_handler: logging.Handler, otel_calls: List[str]
) -> None:
    init_telemetry("slack-web")
    assert any(isinstance(f, SanitizingLogFilter) for f in root_handler.filters)
    assert otel_calls == []


def test_otel_starts_when_an_otlp_endpoint_is_set(
    root_handler: logging.Handler, otel_calls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    init_telemetry("slack-web")
    assert otel_calls == ["slack-web"]


def test_otel_sdk_disabled_wins(
    root_handler: logging.Handler, otel_calls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    init_telemetry("slack-web")
    assert otel_calls == []
