# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for GenerateProgress stage lifecycle."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from nw_cli.progress import GenerateProgress, StageStatus


def _progress() -> GenerateProgress:
    return GenerateProgress(console=Console(file=io.StringIO(), force_terminal=False, width=80))


def test_mark_skipped_sets_status() -> None:
    gp = _progress()
    gp.mark_skipped("wheel")
    wheel = next(s for s in gp.stages if s.key == "wheel")
    assert wheel.skipped is True
    assert wheel.status == StageStatus.SKIPPED


def test_run_stage_success_and_skip() -> None:
    gp = _progress()
    gp.mark_skipped("wire")
    with gp:
        assert gp.run_stage("wire", lambda: 99) is None
        result = gp.run_stage("connector", lambda: 42)
    assert result == 42
    connector = next(s for s in gp.stages if s.key == "connector")
    assert connector.status == StageStatus.DONE
    wire = next(s for s in gp.stages if s.key == "wire")
    assert wire.status == StageStatus.SKIPPED
    out = gp.console.file.getvalue()  # type: ignore[union-attr]
    assert "Completed:" in out
    assert "Skipped:" in out


def test_run_stage_failure_marks_failed_and_reraises() -> None:
    gp = _progress()
    with gp:
        with pytest.raises(RuntimeError, match="boom"):
            gp.run_stage("mcp", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    mcp = next(s for s in gp.stages if s.key == "mcp")
    assert mcp.status == StageStatus.FAILED
    assert mcp.error == "boom"
    out = gp.console.file.getvalue()  # type: ignore[union-attr]
    assert "Failed at stage" in out
    assert "boom" in out


def test_log_writes_above_progress() -> None:
    gp = _progress()
    with gp:
        gp.log("hello-stage")
    assert "hello-stage" in gp.console.file.getvalue()  # type: ignore[union-attr]
