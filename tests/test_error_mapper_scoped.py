#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ErrorMapper's per-connector scoping.

Regression coverage for a real bug found in a doc-quality walkthrough: with a
single process-global registry keyed only by exception type, one connector's
mapping for a shared exception type (e.g. ``httpx.HTTPStatusError``) could
silently "win" and be returned for a completely unrelated connector's error,
depending on which connector's module happened to import last. These tests
use synthetic exception types and connector ids (never real ones) so they
can't collide with the process-wide registrations any real connector module
performs elsewhere in the suite.
"""

from __future__ import annotations

import pytest

from node_wire_runtime.errors import ErrorMapper
from node_wire_runtime.models import ErrorCategory


class _SharedError(Exception):
    """Stands in for a vendor exception type two unrelated connectors both raise."""


class _NarrowError(_SharedError):
    """A more specific subtype of ``_SharedError``."""


class _UnmappedError(Exception):
    """Registered by neither connector nor the global registry."""


class _RuntimeWideError(Exception):
    """Stands in for a runtime-raised exception like PolicyDenied."""


def test_two_connectors_registering_the_same_exception_type_do_not_leak() -> None:
    """The bug this module exists to catch: connector A must never see connector B's code."""
    ErrorMapper.register("wf_connector_a", _SharedError, ErrorCategory.BUSINESS, code="A_ERROR")
    ErrorMapper.register("wf_connector_b", _SharedError, ErrorCategory.FATAL, code="B_ERROR")

    exc = _SharedError("boom")

    resolved_a = ErrorMapper.resolve(exc, connector_id="wf_connector_a")
    resolved_b = ErrorMapper.resolve(exc, connector_id="wf_connector_b")

    assert resolved_a.code == "A_ERROR"
    assert resolved_a.category == ErrorCategory.BUSINESS
    assert resolved_b.code == "B_ERROR"
    assert resolved_b.category == ErrorCategory.FATAL


def test_unscoped_connector_does_not_inherit_another_connectors_mapping() -> None:
    """A connector that never registered this exception type must not see anyone else's code."""
    ErrorMapper.register("wf_connector_c", _SharedError, ErrorCategory.BUSINESS, code="C_ERROR")

    resolved = ErrorMapper.resolve(_SharedError("boom"), connector_id="wf_connector_d")

    assert resolved.code != "C_ERROR"
    assert resolved.code == "_SharedError"
    assert resolved.category == ErrorCategory.FATAL


def test_resolve_falls_back_to_global_registry() -> None:
    """Runtime-wide exceptions (registered without a connector_id) resolve for every connector."""
    ErrorMapper.register_global(_RuntimeWideError, ErrorCategory.AUTH, code="RUNTIME_WIDE")

    resolved = ErrorMapper.resolve(_RuntimeWideError("denied"), connector_id="wf_connector_e")

    assert resolved.code == "RUNTIME_WIDE"
    assert resolved.category == ErrorCategory.AUTH


def test_connector_scope_takes_priority_over_global_scope() -> None:
    """A connector's own mapping for a type wins over a same-type global mapping."""
    ErrorMapper.register_global(_SharedError, ErrorCategory.FATAL, code="GLOBAL_SHARED")
    ErrorMapper.register("wf_connector_f", _SharedError, ErrorCategory.BUSINESS, code="F_ERROR")

    resolved = ErrorMapper.resolve(_SharedError("boom"), connector_id="wf_connector_f")

    assert resolved.code == "F_ERROR"


def test_resolve_defaults_to_fatal_with_type_name_when_unmapped() -> None:
    resolved = ErrorMapper.resolve(_UnmappedError("mystery"), connector_id="wf_connector_g")

    assert resolved.code == "_UnmappedError"
    assert resolved.category == ErrorCategory.FATAL


def test_resolve_picks_the_most_specific_registered_type() -> None:
    """A broadly-registered parent type must not shadow a narrower, more specific one.

    Registered deliberately out of specificity order (broad first) to prove the
    match isn't first-hit-in-insertion-order.
    """
    ErrorMapper.register("wf_connector_h", _SharedError, ErrorCategory.FATAL, code="BROAD")
    ErrorMapper.register("wf_connector_h", _NarrowError, ErrorCategory.RETRYABLE, code="NARROW")

    resolved = ErrorMapper.resolve(_NarrowError("boom"), connector_id="wf_connector_h")

    assert resolved.code == "NARROW"
    assert resolved.category == ErrorCategory.RETRYABLE

    # An instance of the broad type alone still gets the broad mapping.
    resolved_broad = ErrorMapper.resolve(_SharedError("boom"), connector_id="wf_connector_h")
    assert resolved_broad.code == "BROAD"


def test_resolve_requires_connector_id() -> None:
    with pytest.raises(TypeError):
        ErrorMapper.resolve(_UnmappedError("mystery"))  # type: ignore[call-arg]
