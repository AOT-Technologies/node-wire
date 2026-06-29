#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging

from node_wire_runtime.log_sanitization import (
    REDACTED,
    SanitizingLogFilter,
    install_sanitizing_log_filter,
    sanitize_value,
)


def test_sanitize_value_redacts_search_params_dict() -> None:
    value = {"family": "Smith", "given": "John"}
    assert sanitize_value("search_params", value) == REDACTED


def test_sanitizing_log_filter_redacts_extra_search_params() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="search",
        args=(),
        exc_info=None,
    )
    record.search_params = {"family": "Smith"}
    SanitizingLogFilter().filter(record)
    assert record.search_params == REDACTED


def test_sanitizing_log_filter_redacts_long_body_arg() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed | body=%s",
        args=("PHI_MARKER_" + ("x" * 120),),
        exc_info=None,
    )
    SanitizingLogFilter().filter(record)
    assert record.args[0] == REDACTED
    assert "PHI_MARKER_" not in str(record.args[0])


def test_install_sanitizing_log_filter_is_idempotent() -> None:
    install_sanitizing_log_filter()
    root = logging.getLogger()
    count_before = sum(1 for flt in root.filters if isinstance(flt, SanitizingLogFilter))
    install_sanitizing_log_filter()
    count_after = sum(1 for flt in root.filters if isinstance(flt, SanitizingLogFilter))
    assert count_before == count_after
    assert count_after >= 1
