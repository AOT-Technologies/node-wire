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
    scrub_secrets,
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


def _record(**extras: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extras)
    return record


def test_credential_extras_are_redacted() -> None:
    record = _record(
        authorization="Bearer abc123",
        api_key="sk-live-1",
        access_token="tok",
        session_cookie="c=1",
        static_credentials={"user": "u"},
    )
    SanitizingLogFilter().filter(record)
    assert record.authorization == REDACTED
    assert record.api_key == REDACTED
    assert record.access_token == REDACTED
    assert record.session_cookie == REDACTED
    assert record.static_credentials == REDACTED


def test_token_counts_still_log() -> None:
    record = _record(prompt_tokens=12, total_tokens=30)
    SanitizingLogFilter().filter(record)
    assert record.prompt_tokens == 12
    assert record.total_tokens == 30


def test_error_message_loses_query_string_and_bearer_token() -> None:
    record = _record(
        error_message=(
            "Client error '401 Unauthorized' for url "
            "'https://user:pw@api.x.com/v1/items?api_key=sk-live-2#frag': Bearer abc.def-123"
        )
    )
    SanitizingLogFilter().filter(record)
    assert record.error_message == (
        f"Client error '401 Unauthorized' for url 'https://api.x.com/v1/items': Bearer {REDACTED}"
    )


def test_unformatted_message_and_string_args_are_scrubbed() -> None:
    plain = _record()
    plain.msg = "GET https://api.x.com/v1?api_key=sk-1 failed"
    SanitizingLogFilter().filter(plain)
    assert plain.getMessage() == "GET https://api.x.com/v1 failed"

    with_args = _record()
    with_args.msg, with_args.args = "GET %s failed", ("https://api.x.com/v1?api_key=sk-1",)
    SanitizingLogFilter().filter(with_args)
    assert with_args.getMessage() == "GET https://api.x.com/v1 failed"


def test_format_string_with_url_placeholder_is_left_intact() -> None:
    record = _record()
    record.msg, record.args = "GET https://api.x.com/v1?page=%s", (2,)
    SanitizingLogFilter().filter(record)
    assert record.getMessage() == "GET https://api.x.com/v1?page=2"


def test_scrub_secrets_leaves_plain_text_alone() -> None:
    assert scrub_secrets("Connection refused") == "Connection refused"


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


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_install_redacts_records_propagated_from_child_loggers() -> None:
    """Root-logger filters never see propagated records, so redaction must sit on the handlers."""
    root = logging.getLogger()
    original_filters = list(root.filters)
    handler = _CapturingHandler()
    root.addHandler(handler)
    try:
        install_sanitizing_log_filter()
        token = set_log_connector_id("slack_web")
        try:
            logging.getLogger("runtime.base_connector").warning(
                "failed", extra={"password": "hunter2", "trace_id": "t-1"}
            )
        finally:
            reset_log_connector_id(token)
        (record,) = handler.records
        assert record.password == REDACTED
        assert record.trace_id == "t-1"
        assert record.connector_id == "slack_web"
    finally:
        root.removeHandler(handler)
        for flt in list(root.filters):
            root.removeFilter(flt)
        for flt in original_filters:
            root.addFilter(flt)
