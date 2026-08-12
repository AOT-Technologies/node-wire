# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Missing-prerequisite handling: TTY prompt vs hard error."""

from __future__ import annotations

import sys
from collections.abc import Callable

import typer
from rich.console import Console
from rich.prompt import Confirm

console = Console(stderr=True)


def is_interactive() -> bool:
    """True when stdin is a TTY (overridable in tests)."""
    return sys.stdin.isatty()


def ensure(
    condition: bool,
    *,
    prompt: str,
    fix_command: str,
    build_fn: Callable[[], None],
) -> None:
    """If *condition* is false, prompt (TTY) or abort (non-TTY).

    On interactive yes, call *build_fn*. On no / non-TTY, exit non-zero.
    """
    if condition:
        return

    if is_interactive():
        if Confirm.ask(prompt, default=False, console=console):
            build_fn()
            return
        console.print(f"[#e01d5a]Aborted.[/#e01d5a] Fix with: [bold]{fix_command}[/bold]")
        raise typer.Exit(1)

    console.print(
        f"[bold #e01d5a]error:[/bold #e01d5a] {prompt.rstrip('?')}.\n"
        f"  Fix: [bold]{fix_command}[/bold]"
    )
    raise typer.Exit(1)
