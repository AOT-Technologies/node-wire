# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Arrow-key selection from a list of options, drawn with Rich.

↑/↓ (or k/j) move, Enter selects, an option's shortcut letter selects it at once, Ctrl-C
interrupts. Without an interactive terminal it falls back to a typed choice.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.prompt import Prompt
from rich.text import Text

from nw_cli.ui import AMBER, BLUE

UP, DOWN, ENTER = "up", "down", "enter"


@dataclass(frozen=True)
class Option:
    key: str  # shortcut letter, also the return value
    label: str
    hint: str = ""


_KEYS = {"\x1b[A": UP, "\x1b[B": DOWN, "\x1bOA": UP, "\x1bOB": DOWN, "\r": ENTER, "\n": ENTER}


@contextmanager
def _posix_keys() -> Iterator[Callable[[], str]]:
    """Keys from stdin in cbreak mode, held for the whole menu so type-ahead is not echoed.

    cbreak (not raw) keeps output processing for the Rich display and lets Ctrl-C raise
    KeyboardInterrupt as usual.
    """
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def read() -> str:
        char = os.read(fd, 1).decode(errors="ignore")
        # An arrow key is ESC [ A; a lone Esc has nothing after it.
        if char == "\x1b" and select.select([fd], [], [], 0.05)[0]:
            char += os.read(fd, 2).decode(errors="ignore")
        return _KEYS.get(char, char)

    try:
        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@contextmanager
def _windows_keys() -> Iterator[Callable[[], str]]:
    import msvcrt

    def read() -> str:
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return {"H": UP, "P": DOWN}.get(msvcrt.getwch(), "")
        if char == "\x03":
            raise KeyboardInterrupt
        return _KEYS.get(char, char)

    yield read


def terminal_keys() -> AbstractContextManager[Callable[[], str]]:
    return _windows_keys() if os.name == "nt" else _posix_keys()


def _interactive(console: Console) -> bool:
    return sys.stdin.isatty() and console.is_terminal


def key_session(console: Console) -> AbstractContextManager[Callable[[], str] | None]:
    """Keys for several menus in a row (pass the reader to :func:`choose`), None without a
    terminal. Holding one session keeps keys typed between two menus from being lost."""
    return terminal_keys() if _interactive(console) else nullcontext(None)


def _render(title: str, options: Sequence[Option], index: int) -> Group:
    lines = [Text.assemble((f"{title} ", "bold"), ("↑/↓ move · Enter select", "dim"))]
    for i, option in enumerate(options):
        line = Text()
        if i == index:
            line.append("❯ ", style=f"bold {AMBER}")
            line.append(option.label, style=f"bold {AMBER}")
        else:
            line.append("  ")
            line.append(option.label)
        if option.hint:
            line.append(f"  {option.hint}", style="dim")
        lines.append(line)
    return Group(*lines)


def choose(
    console: Console,
    title: str,
    options: Sequence[Option],
    *,
    default: int = 0,
    key_reader: Callable[[], str] | None = None,
) -> str:
    """Let the user pick one of ``options``; returns its ``key``."""
    interactive = key_reader is not None or _interactive(console)
    if not interactive:
        return Prompt.ask(
            f"{title} [dim]({', '.join(f'{o.key} = {o.label}' for o in options)})[/dim]",
            choices=[o.key for o in options],
            default=options[default].key,
            console=console,
        )
    keys = nullcontext(key_reader) if key_reader is not None else terminal_keys()
    index = default
    with (
        keys as reader,
        Live(_render(title, options, index), console=console, transient=True) as live,
    ):
        while True:
            key = reader()
            if key == "ctrl-c":
                raise KeyboardInterrupt
            if key in (UP, "k"):
                index = (index - 1) % len(options)
            elif key in (DOWN, "j"):
                index = (index + 1) % len(options)
            elif key == ENTER:
                break
            else:
                match = next((i for i, o in enumerate(options) if o.key == key.lower()), None)
                if match is not None:
                    index = match
                    break
            live.update(_render(title, options, index))
    console.print(
        Text.assemble(("✓ ", BLUE), (f"{title} ", "bold"), options[index].label), highlight=False
    )
    return options[index].key
