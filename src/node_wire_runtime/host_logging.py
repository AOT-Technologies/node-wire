#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Process logging for a node-wire host (an MCP server, a REST/gRPC entrypoint).

The runtime logs its error taxonomy and trace as stdlib ``extra`` fields (``trace_id``,
``error_code``, ``error_category``, ``audit_event`` ...). A plain ``logging.Formatter`` drops
them, so hosts that own their console output use :class:`ExtraFieldsFormatter`.
"""

from __future__ import annotations

import logging
import os
import sys

from node_wire_runtime.log_sanitization import (
    _LOG_RECORD_STANDARD_KEYS,
    install_sanitizing_log_filter,
)
from node_wire_runtime.observability import init_observability

_OTLP_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
# Added by logging itself or by uvicorn; never runtime fields.
_NOT_FIELDS = _LOG_RECORD_STANDARD_KEYS | {"asctime", "color_message"}


class ExtraFieldsFormatter(logging.Formatter):
    """``<time> <LEVEL> [<logger>] <message> key=value ...`` with every ``extra`` field."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = [
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in _NOT_FIELDS and value not in ("", None)
        ]
        if not fields:
            return line
        head, sep, tail = line.partition("\n")  # keep tracebacks below the fields
        return f"{head} {' '.join(fields)}{sep}{tail}"


def otlp_configured() -> bool:
    """Whether an OTLP endpoint is set and the SDK is not disabled (``OTEL_SDK_DISABLED``)."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    return any(os.environ.get(name, "").strip() for name in _OTLP_ENDPOINT_VARS)


def configure_host_logging(service_name: str, *, level: str = "INFO") -> None:
    """Call once at host startup.

    Adds a stderr handler with :class:`ExtraFieldsFormatter` unless the root logger already has
    handlers (a host that configures its own keeps them), installs node-wire's redaction and
    ``connector_id`` stamping on the root handlers, and starts OpenTelemetry export
    (``service.name`` = ``service_name``) only when an OTLP endpoint is configured, so a host
    without a collector does not log exporter connection errors.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(ExtraFieldsFormatter())
        root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    install_sanitizing_log_filter()
    if otlp_configured():
        init_observability(app_name=service_name)
