# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for missing-prerequisite ensure()."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from nw_cli.prerequisites import ensure


def test_ensure_ok_when_condition_true() -> None:
    build = MagicMock()
    ensure(True, prompt="missing?", fix_command="nw wheel", build_fn=build)
    build.assert_not_called()


def test_ensure_non_tty_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nw_cli.prerequisites.is_interactive", lambda: False)
    build = MagicMock()
    with pytest.raises(typer.Exit) as exc:
        ensure(False, prompt="Wheel missing?", fix_command="nw wheel --runtime", build_fn=build)
    assert exc.value.exit_code == 1
    build.assert_not_called()


def test_ensure_tty_yes_calls_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nw_cli.prerequisites.is_interactive", lambda: True)
    monkeypatch.setattr("nw_cli.prerequisites.Confirm.ask", lambda *a, **k: True)
    build = MagicMock()
    ensure(False, prompt="build?", fix_command="nw wheel", build_fn=build)
    build.assert_called_once()


def test_ensure_tty_no_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nw_cli.prerequisites.is_interactive", lambda: True)
    monkeypatch.setattr("nw_cli.prerequisites.Confirm.ask", lambda *a, **k: False)
    build = MagicMock()
    with pytest.raises(typer.Exit) as exc:
        ensure(False, prompt="build?", fix_command="nw wheel", build_fn=build)
    assert exc.value.exit_code == 1
    build.assert_not_called()
