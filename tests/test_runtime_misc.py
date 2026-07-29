#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for rate_limit, streaming, log_sanitization gaps, observability
wrappers, and connector_registry error paths."""

from __future__ import annotations

import logging
from importlib.metadata import EntryPoint
from unittest.mock import MagicMock, patch

import pytest

from node_wire_runtime import connector_registry
from node_wire_runtime.log_sanitization import (
    REDACTED,
    SanitizingLogFilter,
    _redact_sensitive_string_arg,
    install_sanitizing_log_filter,
    is_sensitive_key,
    sanitize_log_record,
    sanitize_mapping,
    sanitize_value,
)
from node_wire_runtime.rate_limit import RateLimitExceeded, TokenBucket
from node_wire_runtime.streaming import (
    BufferedStreamIterator,
    resolve_stream_buffer_ms,
    stream_completion_log,
)


# ---------------------------------------------------------------------------
# TokenBucket — acquire paths
# ---------------------------------------------------------------------------


async def test_token_bucket_acquire_success() -> None:
    bucket = TokenBucket(capacity=10, refill_rate=1)
    await bucket.acquire(1)
    assert bucket.tokens == 9


async def test_token_bucket_acquire_multiple() -> None:
    bucket = TokenBucket(capacity=10, refill_rate=1)
    await bucket.acquire(5)
    assert bucket.tokens == 5


async def test_token_bucket_acquire_raises_when_exhausted() -> None:
    bucket = TokenBucket(capacity=1, refill_rate=0)
    await bucket.acquire(1)
    with pytest.raises(RateLimitExceeded):
        await bucket.acquire(1)


async def test_token_bucket_refills_over_time() -> None:
    bucket = TokenBucket(capacity=10, refill_rate=1000)
    await bucket.acquire(10)
    assert bucket.tokens == 0
    # Artificially push last_refill back to simulate elapsed time
    bucket.last_refill -= 1.0  # 1 second elapsed → 1000 tokens added, capped at 10
    await bucket.acquire(5)
    assert bucket.tokens >= 4  # at least 5 refilled, minus 5 acquired


async def test_token_bucket_exceed_raises_with_message() -> None:
    bucket = TokenBucket(capacity=0, refill_rate=0)
    with pytest.raises(RateLimitExceeded, match="rate limit exceeded"):
        await bucket.acquire(1)


# ---------------------------------------------------------------------------
# resolve_stream_buffer_ms
# ---------------------------------------------------------------------------


def test_resolve_stream_buffer_ms_override_used() -> None:
    assert resolve_stream_buffer_ms(200) == 200


def test_resolve_stream_buffer_ms_override_clamped_to_zero() -> None:
    assert resolve_stream_buffer_ms(-50) == 0


def test_resolve_stream_buffer_ms_override_clamped_to_max() -> None:
    assert resolve_stream_buffer_ms(99999) == 30000


def test_resolve_stream_buffer_ms_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_STREAM_BUFFER_MS", "500")
    assert resolve_stream_buffer_ms() == 500


def test_resolve_stream_buffer_ms_env_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_STREAM_BUFFER_MS", "notanumber")
    assert resolve_stream_buffer_ms() == 0


# ---------------------------------------------------------------------------
# stream_completion_log
# ---------------------------------------------------------------------------


def test_stream_completion_log_success(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="runtime.streaming"):
        stream_completion_log("tid-1", True, connector_id="smtp", action="send")
    assert any("completed" in r.message.lower() for r in caplog.records)


def test_stream_completion_log_failure(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="runtime.streaming"):
        stream_completion_log("tid-2", False, connector_id="smtp", action="send")
    assert any("failed" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# BufferedStreamIterator
# ---------------------------------------------------------------------------


async def _collect_async(gen) -> list:
    items = []
    async for item in gen:
        items.append(item)
    return items


async def test_buffered_stream_no_buffer() -> None:
    async def source():
        for i in range(3):
            yield {"i": i}

    items = await _collect_async(BufferedStreamIterator(source(), buffer_ms=0, trace_id="t1"))
    assert items == [{"i": 0}, {"i": 1}, {"i": 2}]


async def test_buffered_stream_with_buffer() -> None:
    async def source():
        for i in range(4):
            yield {"i": i}

    items = await _collect_async(BufferedStreamIterator(source(), buffer_ms=1000, trace_id="t2"))
    assert items == [{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}]


async def test_buffered_stream_logs_on_failure() -> None:
    async def bad_source():
        yield {"i": 0}
        raise RuntimeError("upstream error")

    with pytest.raises(RuntimeError, match="upstream error"):
        await _collect_async(BufferedStreamIterator(bad_source(), buffer_ms=0, trace_id="t3"))


async def test_buffered_stream_flush_mid_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a mid-stream flush by rolling back last_flush time."""
    import time

    call_count = 0
    real_monotonic = time.monotonic

    def patched_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        # First call (setup) returns normal; subsequent calls return far future
        if call_count <= 2:
            return real_monotonic()
        return real_monotonic() + 10.0

    async def source():
        for i in range(3):
            yield {"i": i}

    with patch("node_wire_runtime.streaming.time.monotonic", side_effect=patched_monotonic):
        items = await _collect_async(BufferedStreamIterator(source(), buffer_ms=100, trace_id="t4"))
    assert len(items) == 3


# ---------------------------------------------------------------------------
# log_sanitization gaps
# ---------------------------------------------------------------------------


def test_is_sensitive_key_substring_match() -> None:
    assert is_sensitive_key("patient_id") is True
    assert is_sensitive_key("my_ssn") is True
    assert is_sensitive_key("email_address") is True


def test_is_sensitive_key_always_redact() -> None:
    assert is_sensitive_key("search_params") is True
    assert is_sensitive_key("body") is True
    assert is_sensitive_key("payload") is True


def test_is_sensitive_key_safe_key() -> None:
    assert is_sensitive_key("connector_id") is False
    assert is_sensitive_key("action") is False


def test_sanitize_value_nested_dict() -> None:
    val = {"safe_key": {"nested": "value"}, "patient_id": "123"}
    result = sanitize_value("outer", val)
    assert result["patient_id"] == REDACTED
    assert result["safe_key"] == {"nested": "value"}


def test_sanitize_value_list() -> None:
    # List elements are recursively processed with the same key
    result = sanitize_value("items", [{"secret": "s"}, {"ok": "v"}])
    assert isinstance(result, list)
    # "secret" key inside the nested dict gets redacted
    assert result[0]["secret"] == REDACTED
    assert result[1]["ok"] == "v"


def test_sanitize_value_tuple() -> None:
    result = sanitize_value("data", ("safe", "value"))
    assert isinstance(result, tuple)
    assert result == ("safe", "value")


def test_sanitize_value_str_body_key_redacted() -> None:
    result = sanitize_value("body", "any string content")
    assert result == REDACTED


def test_sanitize_value_str_non_body_key_passthrough() -> None:
    result = sanitize_value("action", "send_email")
    assert result == "send_email"


def test_sanitize_mapping_redacts_sensitive() -> None:
    result = sanitize_mapping({"patient": "Smith", "connector_id": "smtp"})
    assert result["patient"] == REDACTED
    assert result["connector_id"] == "smtp"


def test_redact_sensitive_string_arg_long_string() -> None:
    long_str = "x" * 101
    assert _redact_sensitive_string_arg(long_str) == REDACTED


def test_redact_sensitive_string_arg_phi_marker() -> None:
    assert _redact_sensitive_string_arg("contains_phi_marker_here") == REDACTED


def test_redact_sensitive_string_arg_safe() -> None:
    assert _redact_sensitive_string_arg("connector_id=smtp") == "connector_id=smtp"


def test_sanitize_log_record_dict_args() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%(patient)s %(action)s",
        args={"patient": "Smith", "action": "send"},
        exc_info=None,
    )
    sanitize_log_record(record)
    assert record.args["patient"] == REDACTED  # type: ignore[index]
    assert record.args["action"] == "send"  # type: ignore[index]


def test_sanitize_log_record_tuple_args() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s %s",
        args=("phi_marker_data", "safe"),
        exc_info=None,
    )
    sanitize_log_record(record)
    assert record.args[0] == REDACTED  # type: ignore[index]
    assert record.args[1] == "safe"


def test_sanitize_log_record_no_args() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="plain message",
        args=(),
        exc_info=None,
    )
    sanitize_log_record(record)  # should not raise


def test_install_sanitizing_filter_finds_existing_without_flag() -> None:
    """When a SanitizingLogFilter is already present, do not add a duplicate."""
    root = logging.getLogger()
    original_filters = list(root.filters)
    try:
        root.addFilter(SanitizingLogFilter())
        # Count with our filter present; a SanitizingLogFilter may already be
        # installed globally (e.g. bindings_entrypoint installs one at import).
        count_before = sum(1 for f in root.filters if isinstance(f, SanitizingLogFilter))
        install_sanitizing_log_filter()
        count_after = sum(1 for f in root.filters if isinstance(f, SanitizingLogFilter))
        assert count_after == count_before
    finally:
        for f in list(root.filters):
            root.removeFilter(f)
        for f in original_filters:
            root.addFilter(f)


# ---------------------------------------------------------------------------
# observability: SanitizingSpanExporter / SanitizingLogExporter
# ---------------------------------------------------------------------------


def test_sanitizing_span_exporter_redacts_sensitive_attributes() -> None:
    from node_wire_runtime.observability import SanitizingSpanExporter

    delegate = MagicMock()
    delegate.export.return_value = None
    exporter = SanitizingSpanExporter(delegate)

    fake_span = MagicMock()
    fake_span._attributes = {"patient_id": "12345", "connector_id": "smtp"}
    exporter.export([fake_span])

    delegate.export.assert_called_once()
    assert fake_span._attributes["patient_id"] == REDACTED
    assert fake_span._attributes["connector_id"] == "smtp"


def test_sanitizing_span_exporter_shutdown() -> None:
    from node_wire_runtime.observability import SanitizingSpanExporter

    delegate = MagicMock()
    exporter = SanitizingSpanExporter(delegate)
    exporter.shutdown()
    delegate.shutdown.assert_called_once()


def test_sanitizing_span_exporter_force_flush() -> None:
    from node_wire_runtime.observability import SanitizingSpanExporter

    delegate = MagicMock()
    delegate.force_flush.return_value = True
    exporter = SanitizingSpanExporter(delegate)
    result = exporter.force_flush(1000)
    assert result is True
    delegate.force_flush.assert_called_once_with(1000)


def test_sanitizing_span_exporter_force_flush_no_delegate_method() -> None:
    from node_wire_runtime.observability import SanitizingSpanExporter

    delegate = MagicMock(spec=[])  # no force_flush attribute
    exporter = SanitizingSpanExporter(delegate)
    result = exporter.force_flush()
    assert result is True


def test_sanitizing_log_exporter_redacts_attributes() -> None:
    from node_wire_runtime.observability import SanitizingLogExporter

    delegate = MagicMock()
    exporter = SanitizingLogExporter(delegate)

    fake_record = MagicMock()
    fake_record.attributes = {"email": "user@example.com", "action": "send"}
    exporter.export([fake_record])

    delegate.export.assert_called_once()
    assert fake_record.attributes["email"] == REDACTED
    assert fake_record.attributes["action"] == "send"


def test_sanitizing_log_exporter_shutdown() -> None:
    from node_wire_runtime.observability import SanitizingLogExporter

    delegate = MagicMock()
    exporter = SanitizingLogExporter(delegate)
    exporter.shutdown()
    delegate.shutdown.assert_called_once()


def test_sanitizing_log_exporter_force_flush() -> None:
    from node_wire_runtime.observability import SanitizingLogExporter

    delegate = MagicMock()
    delegate.force_flush.return_value = True
    exporter = SanitizingLogExporter(delegate)
    result = exporter.force_flush(500)
    assert result is True


def test_sanitizing_log_exporter_force_flush_no_delegate() -> None:
    from node_wire_runtime.observability import SanitizingLogExporter

    delegate = MagicMock(spec=[])
    exporter = SanitizingLogExporter(delegate)
    result = exporter.force_flush()
    assert result is True


def test_otel_context_filter_with_valid_span() -> None:
    from opentelemetry.sdk.trace import TracerProvider

    from node_wire_runtime.observability import _OtelContextFilter

    flt = _OtelContextFilter()
    provider = TracerProvider()
    tracer = provider.get_tracer("test")

    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    with tracer.start_as_current_span("test-span"):
        result = flt.filter(record)

    assert result is True
    assert len(record.otel_trace_id) == 32
    assert len(record.otel_span_id) == 16


# ---------------------------------------------------------------------------
# connector_registry — error paths
# ---------------------------------------------------------------------------


def test_parse_allowed_names_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NW_ALLOWED_CONNECTORS", raising=False)
    result = connector_registry._parse_allowed_names()
    assert result == set()


def test_parse_allowed_names_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "  ")
    result = connector_registry._parse_allowed_names()
    assert result == set()


def test_registration_module_missing_is_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ModuleNotFoundError with name == reg_name is silently ignored."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "myconn")
    ep = EntryPoint(name="myconn", value="node_wire_myconn.logic", group="node_wire.connectors")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_myconn.logic":
            return MagicMock()
        err = ModuleNotFoundError(f"No module named '{name}'")
        err.name = name  # type: ignore[attr-defined]
        raise err

    with (
        patch.object(connector_registry, "entry_points", return_value=[ep]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
    ):
        loaded = connector_registry.auto_register()

    assert "node_wire_myconn.logic" in loaded
    assert "node_wire_myconn.registration" not in loaded


def test_registration_dep_error_is_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    """ModuleNotFoundError for a dep inside registration module is re-raised."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "myconn2")
    ep = EntryPoint(name="myconn2", value="node_wire_myconn2.logic", group="node_wire.connectors")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_myconn2.logic":
            return MagicMock()
        err = ModuleNotFoundError("No module named 'missing_dep'")
        err.name = "missing_dep"  # type: ignore[attr-defined]
        raise err

    with (
        patch.object(connector_registry, "entry_points", return_value=[ep]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
        pytest.raises(ModuleNotFoundError),
    ):
        connector_registry.auto_register()


def test_registration_unexpected_exception_is_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "myconn3")
    ep = EntryPoint(name="myconn3", value="node_wire_myconn3.logic", group="node_wire.connectors")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_myconn3.logic":
            return MagicMock()
        raise RuntimeError("unexpected error in registration module")

    with (
        patch.object(connector_registry, "entry_points", return_value=[ep]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
        pytest.raises(RuntimeError, match="unexpected error"),
    ):
        connector_registry.auto_register()


def test_fallback_logic_missing_dep_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback logic module raises ModuleNotFoundError for an internal dep → re-raise."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "badconn")

    def fake_import(name: str) -> MagicMock:
        err = ModuleNotFoundError("No module named 'some_dep'")
        err.name = "some_dep"  # type: ignore[attr-defined]
        raise err

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
        pytest.raises(ModuleNotFoundError),
    ):
        connector_registry.auto_register()


def test_fallback_logic_not_found_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback logic module genuinely absent → skip, no error."""
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "absent_connector")

    def fake_import(name: str) -> MagicMock:
        err = ModuleNotFoundError(f"No module named '{name}'")
        err.name = name  # type: ignore[attr-defined]
        raise err

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
    ):
        loaded = connector_registry.auto_register()

    assert loaded == []


def test_fallback_registration_missing_silently_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "noregconn")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_noregconn.logic":
            return MagicMock()
        err = ModuleNotFoundError(f"No module named '{name}'")
        err.name = name  # type: ignore[attr-defined]
        raise err

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
    ):
        loaded = connector_registry.auto_register()

    assert "node_wire_noregconn.logic" in loaded
    assert "node_wire_noregconn.registration" not in loaded


def test_fallback_registration_dep_error_reraised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "regerrconn")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_regerrconn.logic":
            return MagicMock()
        if name == "node_wire_regerrconn.registration":
            err = ModuleNotFoundError("No module named 'dep_x'")
            err.name = "dep_x"  # type: ignore[attr-defined]
            raise err
        raise ImportError(f"unexpected: {name}")

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
        pytest.raises(ModuleNotFoundError),
    ):
        connector_registry.auto_register()


def test_fallback_registration_unexpected_exception_reraised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NW_ALLOWED_CONNECTORS", "excconn")

    def fake_import(name: str) -> MagicMock:
        if name == "node_wire_excconn.logic":
            return MagicMock()
        raise ValueError("unexpected registration failure")

    with (
        patch.object(connector_registry, "entry_points", return_value=[]),
        patch.object(connector_registry.importlib, "import_module", side_effect=fake_import),
        pytest.raises(ValueError, match="unexpected registration failure"),
    ):
        connector_registry.auto_register()
