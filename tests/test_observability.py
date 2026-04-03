"""Tests for runtime.observability (OpenTelemetry bootstrap)."""
from __future__ import annotations

import logging
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

import node_wire_runtime.observability as obs


@contextmanager
def _ensure_traceloop_stub_modules() -> Iterator[None]:
    """
    unittest.mock.patch('traceloop.sdk.Traceloop') imports the traceloop package.
    When traceloop-sdk is not installed (e.g. global pytest), register minimal
    stubs so patch can bind; remove them only if we added them.
    """
    added: list[str] = []
    try:
        if "traceloop" not in sys.modules:
            traceloop_mod = types.ModuleType("traceloop")
            sdk_mod = types.ModuleType("traceloop.sdk")
            traceloop_mod.sdk = sdk_mod  # type: ignore[attr-defined]
            sdk_mod.Traceloop = type("Traceloop", (), {})  # placeholder for patch target
            sys.modules["traceloop"] = traceloop_mod
            sys.modules["traceloop.sdk"] = sdk_mod
            added.extend(["traceloop", "traceloop.sdk"])
        elif "traceloop.sdk" not in sys.modules:
            sdk_mod = types.ModuleType("traceloop.sdk")
            sdk_mod.Traceloop = type("Traceloop", (), {})
            sys.modules["traceloop.sdk"] = sdk_mod
            added.append("traceloop.sdk")
        yield
    finally:
        for key in added:
            sys.modules.pop(key, None)


@pytest.fixture(autouse=True)
def reset_observability_initialized() -> None:
    obs._INITIALIZED = False
    yield
    obs._INITIALIZED = False


@contextmanager
def _observability_test_patches():
    """Patches OTEL setup so tests do not mutate global tracer or break logging."""
    with _ensure_traceloop_stub_modules():
        with (
            patch("opentelemetry.trace.set_tracer_provider"),
            patch("node_wire_runtime.observability.OTLPSpanExporter") as span_exp,
            patch("node_wire_runtime.observability.OTLPLogExporter") as log_exp,
            patch("node_wire_runtime.observability.BatchSpanProcessor"),
            patch("node_wire_runtime.observability.BatchLogRecordProcessor"),
            patch("node_wire_runtime.observability.set_logger_provider"),
            patch(
                "node_wire_runtime.observability.LoggingHandler",
                side_effect=lambda **kwargs: logging.NullHandler(),
            ),
            patch("traceloop.sdk.Traceloop") as mock_tl,
        ):
            mock_tl.init = MagicMock()
            yield span_exp, log_exp, mock_tl


def test_init_observability_idempotent() -> None:
    """Second call should not reconfigure exporters."""
    with _observability_test_patches() as (span_exp, log_exp, _mock_tl):
        obs.init_observability("app-a")
        obs.init_observability("app-b")
    assert span_exp.call_count == 1
    assert log_exp.call_count == 1


def test_init_observability_invalid_sampling_ratio_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AOT_TRACING_SAMPLING_RATIO", "not-a-number")
    with _observability_test_patches():
        with caplog.at_level(logging.WARNING, logger="runtime.observability"):
            obs.init_observability("app-warn")
    assert any("Invalid AOT_TRACING_SAMPLING_RATIO" in r.message for r in caplog.records)


def test_init_observability_otel_headers_passed_to_exporters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "key=value,foo=bar")
    with _observability_test_patches() as (span_exp, log_exp, _mock_tl):
        obs.init_observability("app-h")
    expected_headers = {"key": "value", "foo": "bar"}
    assert span_exp.call_args.kwargs.get("headers") == expected_headers
    assert log_exp.call_args.kwargs.get("headers") == expected_headers


def test_otel_context_filter_sets_empty_trace_when_no_span() -> None:
    flt = obs._OtelContextFilter()
    log = logging.getLogger("test_otel_filter")
    log.addFilter(flt)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", (), None)
    assert flt.filter(record) is True
    assert record.otel_trace_id == ""
    assert record.otel_span_id == ""


def test_init_observability_traceloop_failure_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _observability_test_patches() as (_s, _l, mock_tl):
        mock_tl.init = MagicMock(side_effect=RuntimeError("traceloop unavailable"))
        with caplog.at_level(logging.WARNING, logger="runtime.observability"):
            obs.init_observability("app-tl")
    assert any("Failed to initialize Traceloop" in r.message for r in caplog.records)
