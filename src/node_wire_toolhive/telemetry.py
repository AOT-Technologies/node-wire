# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Process-wide log redaction and OpenTelemetry for a stacklok-built MCP server."""

from __future__ import annotations

from node_wire_runtime.host_logging import otlp_configured
from node_wire_runtime.log_sanitization import install_sanitizing_log_filter
from node_wire_runtime.observability import init_observability


def init_telemetry(service_name: str) -> None:
    """Call once at startup, after the server's own logging handlers are configured.

    The stacklok template owns the console format (structlog), so unlike
    :func:`node_wire_runtime.host_logging.configure_host_logging` this adds no handler. It
    installs node-wire's redaction and ``connector_id`` stamping on the root handlers, and
    starts OpenTelemetry export only when an OTLP endpoint is configured.
    """
    install_sanitizing_log_filter()
    if otlp_configured():
        init_observability(app_name=service_name)
