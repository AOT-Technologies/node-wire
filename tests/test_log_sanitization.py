#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
from __future__ import annotations

import logging

from node_wire_runtime.log_sanitization import (
    REDACTED,
    ConnectorIdLogFilter,
    SanitizingLogFilter,
    connector_id_from_tool_name,
    fhir_log_extra,
    get_log_connector_id,
    install_sanitizing_log_filter,
    reset_log_connector_id,
    sanitize_value,
    set_log_connector_id,
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
    root = logging.getLogger()
    original_filters = list(root.filters)
    try:
        install_sanitizing_log_filter()
        count_before = sum(1 for flt in root.filters if isinstance(flt, SanitizingLogFilter))
        install_sanitizing_log_filter()
        count_after = sum(1 for flt in root.filters if isinstance(flt, SanitizingLogFilter))
        assert count_before == count_after
        assert count_after >= 1
    finally:
        for flt in list(root.filters):
            root.removeFilter(flt)
        for flt in original_filters:
            root.addFilter(flt)


def test_connector_id_from_tool_name() -> None:
    assert connector_id_from_tool_name("google_drive_files_upload") == "google_drive"
    assert connector_id_from_tool_name("google_drive.files.upload") == "google_drive"
    assert connector_id_from_tool_name("fhir_epic_read_patient") == "fhir_epic"
    assert connector_id_from_tool_name("nw_list_tenants") is None
    assert connector_id_from_tool_name("nw.select_config") is None
    assert connector_id_from_tool_name("unknown_tool") is None
    assert connector_id_from_tool_name("") is None


def test_fhir_log_extra_includes_connector_id() -> None:
    extra = fhir_log_extra("t1", mode="read_by_id", connector_id="fhir_epic")
    assert extra["connector_id"] == "fhir_epic"
    assert extra["trace_id"] == "t1"


def test_fhir_log_extra_uses_context_when_omitted() -> None:
    token = set_log_connector_id("fhir_cerner")
    try:
        extra = fhir_log_extra("t2", mode="read_by_search")
        assert extra["connector_id"] == "fhir_cerner"
        assert get_log_connector_id() == "fhir_cerner"
    finally:
        reset_log_connector_id(token)


def test_connector_id_log_filter_injects_from_context() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="inner",
        args=(),
        exc_info=None,
    )
    token = set_log_connector_id("smtp")
    try:
        ConnectorIdLogFilter().filter(record)
        assert record.connector_id == "smtp"
    finally:
        reset_log_connector_id(token)


def test_connector_id_log_filter_does_not_overwrite() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="inner",
        args=(),
        exc_info=None,
    )
    record.connector_id = "stripe"
    token = set_log_connector_id("smtp")
    try:
        ConnectorIdLogFilter().filter(record)
        assert record.connector_id == "stripe"
    finally:
        reset_log_connector_id(token)
