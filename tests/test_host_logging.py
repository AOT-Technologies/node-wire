#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""configure_host_logging: runtime extra fields on the console, redaction, opt-in OTLP."""

from __future__ import annotations

import io
import logging
from typing import Iterator, List

import pytest

from node_wire_runtime import host_logging
from node_wire_runtime.host_logging import configure_host_logging, otlp_configured
from node_wire_runtime.log_sanitization import REDACTED


@pytest.fixture
def clean_root() -> Iterator[logging.Logger]:
    """The root logger, restored afterwards. pytest adds its capture handlers after fixtures
    run, so tests that need an empty root call :func:`_drop_handlers` themselves."""
    root = logging.getLogger()
    saved = (list(root.handlers), list(root.filters), root.level)
    formatters = [(h, h.formatter) for h in root.handlers]
    try:
        yield root
    finally:
        root.handlers[:] = saved[0]
        root.filters[:] = saved[1]
        root.setLevel(saved[2])
        for handler, formatter in formatters:
            handler.setFormatter(formatter)


@pytest.fixture
def otel_calls(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    calls: List[str] = []
    monkeypatch.setattr(host_logging, "init_observability", lambda app_name: calls.append(app_name))
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_SDK_DISABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    return calls


def _drop_handlers(root: logging.Logger) -> None:
    root.handlers[:] = []
    root.filters[:] = []


def test_runtime_fields_are_printed_and_redacted(
    clean_root: logging.Logger, otel_calls: List[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _drop_handlers(clean_root)
    configure_host_logging("nw-slack")
    logging.getLogger("runtime.base_connector").info(
        "Connector execution completed successfully",
        extra={"trace_id": "t-1", "audit_event": "invocation_success", "password": "hunter2"},
    )

    err = capsys.readouterr().err
    assert "INFO [runtime.base_connector] Connector execution completed successfully" in err
    assert "trace_id=t-1" in err and "audit_event=invocation_success" in err
    assert f"password={REDACTED}" in err and "hunter2" not in err
    assert otel_calls == []


def test_an_existing_root_handler_is_kept(
    clean_root: logging.Logger, otel_calls: List[str]
) -> None:
    _drop_handlers(clean_root)
    handler = logging.NullHandler()
    clean_root.addHandler(handler)
    configure_host_logging("nw-slack")
    assert clean_root.handlers == [handler]


def test_an_existing_plain_handler_keeps_its_format_and_gains_the_fields(
    clean_root: logging.Logger, otel_calls: List[str]
) -> None:
    _drop_handlers(clean_root)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    clean_root.addHandler(handler)
    configure_host_logging("nw-slack")
    logging.getLogger("runtime").warning("failed", extra={"error_code": "UPSTREAM_TIMEOUT"})
    assert stream.getvalue() == "WARNING:runtime:failed error_code=UPSTREAM_TIMEOUT\n"


def test_a_custom_formatter_is_left_alone(
    clean_root: logging.Logger, otel_calls: List[str]
) -> None:
    class JsonFormatter(logging.Formatter):
        pass

    _drop_handlers(clean_root)
    handler = logging.StreamHandler(io.StringIO())
    formatter = JsonFormatter()
    handler.setFormatter(formatter)
    clean_root.addHandler(handler)
    configure_host_logging("nw-slack")
    assert handler.formatter is formatter


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, True),
        ({"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://collector:4318/v1/traces"}, True),
        (
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318", "OTEL_SDK_DISABLED": "true"},
            False,
        ),
    ],
)
def test_otlp_is_opt_in(
    otel_calls: List[str], monkeypatch: pytest.MonkeyPatch, env: dict, expected: bool
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert otlp_configured() is expected


def test_otel_starts_with_the_service_name(
    clean_root: logging.Logger, otel_calls: List[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    configure_host_logging("nw-slack")
    assert otel_calls == ["nw-slack"]
