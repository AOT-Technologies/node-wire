# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Missing-prerequisite handling: TTY prompt vs hard error, and tool checks."""

from __future__ import annotations

import functools
import shutil
import subprocess  # nosec B404  # fixed `docker info` argv, no shell
import sys
from collections.abc import Callable

import typer
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm

console = Console(stderr=True)


class PrerequisiteError(RuntimeError):
    """A tool ``nw`` needs is missing or not running."""


def is_interactive() -> bool:
    """True when stdin is a TTY (overridable in tests)."""
    return sys.stdin.isatty()


def confirm_build(prompt: str, *, fix_command: str) -> None:
    """Ask (TTY) to build a missing prerequisite now; otherwise exit 1 with the fix command."""
    if is_interactive():
        if Confirm.ask(prompt, default=False, console=console):
            return
        console.print(
            f"[#e01d5a]Aborted.[/#e01d5a] Fix with: [bold]{escape(fix_command)}[/bold]",
            highlight=False,
        )
        raise typer.Exit(1)

    console.print(
        f"[bold #e01d5a]error:[/bold #e01d5a] {escape(prompt.rstrip('?'))}.\n"
        f"  Fix: [bold]{escape(fix_command)}[/bold]",
        highlight=False,
    )
    raise typer.Exit(1)


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
    confirm_build(prompt, fix_command=fix_command)
    build_fn()


def _probe_docker() -> str | None:
    """Why Docker cannot be used right now, or None when it can."""
    docker = shutil.which("docker")
    if docker is None:
        return "`docker` is not on PATH; install Docker"
    try:
        proc = subprocess.run(  # nosec B603  # fixed argv, no shell
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "the Docker daemon did not answer within 20s"
    except OSError as exc:
        return f"cannot run docker: {exc}"
    if proc.returncode != 0:
        return "the Docker daemon is not running; start Docker Desktop (or the docker service)"
    return None


@functools.cache
def docker_problem() -> str | None:
    """:func:`_probe_docker`, once per process."""
    return _probe_docker()


def require_docker(purpose: str) -> None:
    """Fail fast, with the reason, when ``purpose`` needs Docker and it is unavailable."""
    problem = docker_problem()
    if problem:
        raise PrerequisiteError(f"Docker is required for {purpose}: {problem}")
