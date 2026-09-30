# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Shared terminal output: brand palette, consoles, run options, and the error boundary.

Every ``nw`` command runs inside :func:`guard`, so a failure always ends in one formatted
``error:`` line (or the progress failure panel) and a documented exit code, never a raw traceback.
"""

from __future__ import annotations

import functools
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import typer
from rich.console import Console
from rich.markup import escape

# Node Wire brand palette (docs/stylesheets/extra.css)
AMBER = "#ecb32e"
BLUE = "#37c4f0"
PINK = "#e01d5a"
TEXT = "#E8EDF5"

EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

console = Console()
err_console = Console(stderr=True)

# Typer vendors its own click; its exception base is reachable only through a public subclass.
_CLICK_ERROR = next(c for c in typer.BadParameter.__mro__ if c.__name__ == "ClickException")


def usage_error(message: str, *, flags: str) -> typer.BadParameter:
    """A usage error (exit 2) that click prints under the command's usage line."""
    return typer.BadParameter(message, param_hint=flags)


@dataclass
class RunOptions:
    """Global flags from the ``nw`` callback."""

    verbose: bool = False
    debug: bool = False


options = RunOptions(debug=os.environ.get("NW_DEBUG", "").strip() not in ("", "0", "false"))


def stream_output(target: Console) -> bool:
    """Stream build tool output line by line (``--verbose``, or no terminal as in CI).

    Otherwise it is kept out of the way: the running stage shows its latest line, and a failure
    shows the last lines plus the full log file.
    """
    return options.verbose or not target.is_terminal


class ReportedError(Exception):
    """A failure the progress display has already reported; only the exit code is left."""

    def __init__(self, exit_code: int = EXIT_FAILURE) -> None:
        super().__init__(exit_code)
        self.exit_code = exit_code


def exit_code_for(exc: BaseException) -> int:
    from nw_connector_builder.pipeline import UsageError

    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED
    if isinstance(exc, UsageError):
        return EXIT_USAGE
    return EXIT_FAILURE


def _expected(exc: BaseException) -> bool:
    """Failures with a message written for the user; anything else is a bug in ``nw``."""
    from nw_connector_builder.pipeline import BuildError, UsageError

    from nw_cli.root import RootError
    from nw_cli.stages import StageError

    return isinstance(
        exc,
        (StageError, BuildError, UsageError, RootError, OSError, ValueError, RuntimeError),
    )


def describe(exc: BaseException) -> str:
    """One message for an exception; unexpected ones name their type."""
    message = str(exc).strip() or type(exc).__name__
    if _expected(exc):
        return message
    return f"unexpected {type(exc).__name__}: {message}"


def error(message: str, *, hint: str | None = None) -> None:
    err_console.print(
        f"[bold {PINK}]error:[/bold {PINK}] {escape(message)}", highlight=False, soft_wrap=True
    )
    if hint:
        err_console.print(f"  [dim]{escape(hint)}[/dim]", highlight=False, soft_wrap=True)


def warning(message: str) -> None:
    err_console.print(
        f"[bold {AMBER}]warning:[/bold {AMBER}] {escape(message)}", highlight=False, soft_wrap=True
    )


def show_traceback(exc: BaseException) -> None:
    from rich.traceback import Traceback

    err_console.print(Traceback.from_exception(type(exc), exc, exc.__traceback__))


def debug_hint(exc: BaseException) -> str | None:
    if options.debug:
        return None
    if _expected(exc):
        return "Re-run with --debug for the full traceback."
    return "This is a bug in nw. Re-run with --debug and report the traceback."


F = TypeVar("F", bound=Callable[..., Any])


def guard(fn: F) -> F:
    """Run a command body, turning every failure into a message and an exit code.

    ``typer.Exit`` / click errors pass through (already reported, or usage errors that click
    formats). :class:`ReportedError` exits quietly: the progress panel showed it.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort, _CLICK_ERROR):
            raise
        except ReportedError as exc:
            raise typer.Exit(exc.exit_code) from None
        except KeyboardInterrupt:
            err_console.print(f"\n[bold {AMBER}]Interrupted.[/bold {AMBER}]", highlight=False)
            raise typer.Exit(EXIT_INTERRUPTED) from None
        except Exception as exc:
            if options.debug:
                show_traceback(exc)
            error(describe(exc), hint=debug_hint(exc))
            raise typer.Exit(exit_code_for(exc)) from None

    return wrapper  # type: ignore[return-value]


def new_log_file(command: str) -> Path:
    """A fresh per-run log file: ``<tmp>/nw-logs/<command>-<timestamp>.log``."""
    folder = Path(tempfile.gettempdir()) / "nw-logs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{command}-{stamp}.log"
    suffix = 1
    while path.exists():
        suffix += 1
        path = folder / f"{command}-{stamp}-{suffix}.log"
    return path


def display_path(path: Path | str, base: Path | None = None) -> str:
    """``path`` relative to ``base`` when inside it; URLs as they are.

    ``base`` defaults to the working directory (what the user types from); summaries pass the
    repo root to keep their lines short.
    """
    text = str(path)
    if text.startswith(("http://", "https://")):
        return text
    try:
        return str(Path(path).resolve().relative_to((base or Path.cwd()).resolve()))
    except ValueError:
        return text


@dataclass(frozen=True)
class Step:
    title: str
    command: str | None = None


def next_steps(steps: list[Step], *, heading: str = "Next steps", note: str | None = None) -> None:
    """Numbered follow-up steps; each command on its own line, unwrapped, so it copies cleanly."""
    console.print()
    console.print(f"[bold]{escape(heading)}[/bold]", highlight=False)
    numbered = len(steps) > 1
    for number, step in enumerate(steps, start=1):
        prefix = f"  [bold {BLUE}]{number}[/]  " if numbered else "  "
        console.print(f"{prefix}{escape(step.title)}", highlight=False)
        if step.command:
            indent = "     " if numbered else "    "
            console.print(
                f"{indent}[bold {AMBER}]{escape(step.command)}[/]", highlight=False, soft_wrap=True
            )
    if note:
        console.print(f"  [dim]{escape(note)}[/dim]", highlight=False, soft_wrap=True)
