# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Process-wide log redaction and OpenTelemetry for a stacklok-built MCP server."""

from __future__ import annotations

from node_wire_runtime.host_logging import install_redaction_and_telemetry


def init_telemetry(service_name: str) -> None:
    """Call once at startup, after the server's own logging handlers are configured.

    The stacklok template owns the console format (structlog), so unlike
    :func:`node_wire_runtime.host_logging.configure_host_logging` this adds no handler and
    changes no formatter: see :func:`~node_wire_runtime.host_logging.install_redaction_and_telemetry`.
    """
    install_redaction_and_telemetry(service_name)
