# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Arrow-key option menu."""

from __future__ import annotations

import io
import re
from unittest.mock import patch

import pytest
from rich.console import Console

from nw_cli.menu import DOWN, ENTER, UP, Option, choose

OPTIONS = [Option("g", "Generate"), Option("e", "Edit"), Option("s", "Stop", "resume later")]


def _choose(keys: list[str], default: int = 0) -> tuple[str, str]:
    out = io.StringIO()
    console = Console(file=out, force_terminal=True, width=80)
    pressed = iter(keys)
    return choose(
        console, "Next step", OPTIONS, default=default, key_reader=lambda: next(pressed)
    ), out.getvalue()


def test_enter_picks_the_default() -> None:
    assert _choose([ENTER])[0] == "g"


def test_arrows_move_and_wrap() -> None:
    assert _choose([DOWN, DOWN, ENTER])[0] == "s"
    assert _choose([UP, ENTER])[0] == "s"  # wraps to the last option
    assert _choose([DOWN, DOWN, DOWN, ENTER])[0] == "g"


def test_a_shortcut_letter_picks_at_once_and_echoes_the_choice() -> None:
    key, text = _choose(["e"])
    assert key == "e"
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    assert "✓ Next step Edit" in plain


def test_ctrl_c_interrupts() -> None:
    with pytest.raises(KeyboardInterrupt):
        _choose(["ctrl-c"])


def test_without_a_terminal_it_falls_back_to_a_typed_choice() -> None:
    console = Console(file=io.StringIO(), force_terminal=False)
    with patch("nw_cli.menu.Prompt.ask", return_value="s") as ask:
        assert choose(console, "Next step", OPTIONS) == "s"
    assert ask.call_args.kwargs["choices"] == ["g", "e", "s"]
