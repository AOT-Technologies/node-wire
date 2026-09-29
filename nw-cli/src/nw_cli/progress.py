# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Brand-styled rich.progress display for the multi-stage ``nw`` commands.

Two kinds of output go through it:

* :meth:`GenerateProgress.log` — ``nw``'s own messages, always printed above the bars.
* :meth:`GenerateProgress.output` — build tool output (subprocesses, and anything printed to
  stdout/stderr while the display runs). Streamed with ``--verbose`` or without a terminal;
  otherwise shown as the running stage's latest line. Both go to the run's log file, and a failure
  panel repeats the last lines of the failed stage.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

import typer
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    ProgressBar,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text

from nw_cli.ui import AMBER, BLUE, PINK, TEXT, ReportedError, describe, exit_code_for

__all__ = ["AMBER", "BLUE", "PINK", "TEXT", "GenerateProgress", "Stage", "StageStatus"]

TAIL_LINES = 15


# Eighth-block glyphs for smooth fractional fill ("" 1/8 .. 8/8).
_BAR_BLOCKS = " ▏▎▍▌▋▊▉█"
_TRACK = "░"


class _BlockBar(ProgressBar):
    """A solid, full-height progress bar (█ fill over a ░ track).

    Renders thicker than Rich's default ``━`` line bar, and draws a plain
    track (not the pulsing animation) for not-yet-started tasks.
    """

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = min(self.width or options.max_width, options.max_width)
        track_style = console.get_style(self.style)

        # Active stage: no sub-progress to report, so animate an indeterminate
        # marquee band so the user can see work is happening.
        if self.pulse:
            yield from self._render_marquee(console, width)
            return

        # Pending stage (total unknown / zero): plain track, no animation.
        if not self.total:
            yield Segment(_TRACK * width, track_style)
            return

        ratio = min(1.0, max(0.0, self.completed / self.total))
        is_finished = self.completed >= self.total
        fill_style = console.get_style(self.finished_style if is_finished else self.complete_style)
        eighths = int(round(width * 8 * ratio))
        full, part = divmod(eighths, 8)
        if full:
            yield Segment("█" * full, fill_style)
        if part:
            yield Segment(_BAR_BLOCKS[part], fill_style)
        remaining = width - full - (1 if part else 0)
        if remaining > 0:
            yield Segment(_TRACK * remaining, track_style)

    def _render_marquee(self, console: Console, width: int) -> RenderResult:
        """A block band that sweeps across the track to show live activity."""
        track_style = console.get_style(self.style)
        band_style = console.get_style(self.pulse_style or self.complete_style)
        band = max(4, width // 6)
        speed = 22.0  # cells per second
        span = width + band
        start = int(((self.animation_time or 0.0) * speed) % span) - band

        i = 0
        while i < width:
            lit = start <= i < start + band
            j = i
            while j < width and (start <= j < start + band) == lit:
                j += 1
            char = "█" if lit else _TRACK
            yield Segment(char * (j - i), band_style if lit else track_style)
            i = j


class _BlockBarColumn(BarColumn):
    """BarColumn that renders the thicker :class:`_BlockBar`."""

    def render(self, task: Task) -> _BlockBar:
        # Animate only the active stage: started and not yet complete.
        running = task.started and (task.total is None or task.completed < task.total)
        return _BlockBar(
            total=max(0, task.total) if task.total is not None else None,
            completed=max(0, task.completed),
            width=None if self.bar_width is None else max(1, self.bar_width),
            pulse=running,
            animation_time=task.get_time(),
            style=self.style,
            complete_style=self.complete_style,
            finished_style=self.finished_style,
            pulse_style=self.pulse_style,
        )


class _ActiveSpinnerColumn(SpinnerColumn):
    """Spinner only on the running stage; pending stages show nothing."""

    def render(self, task: Task) -> RenderableType:
        # Skipped stages are added already complete, which Rich does not count as finished.
        done = task.total is not None and task.completed >= task.total
        if task.started and not done:
            return super().render(task)
        return Text(" ")


class _SpacedProgress(Progress):
    """Progress display with a blank line between task rows and the latest output line below."""

    detail: str = ""

    def make_tasks_table(self, tasks):  # type: ignore[override]
        table = super().make_tasks_table(tasks)
        # A bottom pad of 1 (without collapsing) puts a blank line under each
        # row; pad_edge=False keeps it between rows only, not after the last.
        table.padding = (0, 1, 1, 0)
        table.collapse_padding = False
        table.pad_edge = False
        return table

    def get_renderables(self):  # type: ignore[override]
        yield from super().get_renderables()
        if self.detail:
            yield Text(f"  {self.detail}", style="dim", no_wrap=True, overflow="ellipsis")


class _LineWriter(io.TextIOBase):
    """A stdout/stderr stand-in that hands complete lines to a callback."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._buffer += text
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._emit(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            line, self._buffer = self._buffer, ""
            self._emit(line.rstrip("\r"))


class _FdCapture:
    """Route file descriptors 1 and 2 into a callback while the bars are live.

    Child processes that inherit the terminal (``uv lock`` in the stacklok generator, anything a
    library starts without capturing) write to the descriptors directly, past ``sys.stdout``.
    POSIX terminals only; elsewhere those writes still reach the screen as before.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._saved: tuple[int, int] | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def supported(target: Console) -> bool:
        if os.name == "nt" or not target.is_terminal:
            return False
        try:
            return target.file.fileno() == 1
        except (AttributeError, OSError, ValueError):
            return False

    @staticmethod
    def open_terminal() -> TextIO:
        """A stream on the real terminal (a copy of fd 1), valid while capturing."""
        encoding = getattr(sys.__stdout__, "encoding", None) or "utf-8"
        return open(os.dup(1), "w", buffering=1, encoding=encoding)  # noqa: SIM115

    def start(self) -> None:
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        self._saved = (os.dup(1), os.dup(2))
        read, write = os.pipe()
        os.dup2(write, 1)
        os.dup2(write, 2)
        os.close(write)
        self._thread = threading.Thread(target=self._pump, args=(read,), daemon=True)
        self._thread.start()

    def _pump(self, fd: int) -> None:
        # Universal newlines: a \r-redrawn progress line from a build tool becomes separate lines.
        with open(fd, encoding="utf-8", errors="replace") as pipe:
            for line in pipe:
                self._emit(line.rstrip("\n"))

    def stop(self) -> None:
        if self._saved is None:
            return
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        os.dup2(self._saved[0], 1)
        os.dup2(self._saved[1], 2)
        for fd in self._saved:
            os.close(fd)
        self._saved = None
        if self._thread is not None:
            # EOF once no process holds the pipe; a lingering grandchild must not hang nw.
            self._thread.join(timeout=2)
            self._thread = None


class StageStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class Stage:
    key: str
    label: str
    skipped: bool = False
    status: StageStatus = StageStatus.PENDING
    task_id: TaskID | None = None
    error: str | None = None
    # Shown under a failure ("" for none); the default per-key hints are for `nw gen-all`.
    hint: str | None = None
    elapsed: float | None = None


_DEFAULT_HINTS = {
    "connector": "Check the OpenAPI spec path and connector id.",
    "wheel": "Fix with: nw gen-whl --connector-id <id>  (or nw gen-whl --runtime)",
    "mcp": "Ensure wheels exist, then: nw gen-mcp --connector-id <id>",
    "wire": "Check scripts/build-packages.sh ALL_PACKAGES block.",
}


@dataclass
class GenerateProgress:
    """Drive a multi-stage progress display, then print a summary or failure panel.

    An exception escaping the ``with`` block is reported in the failure panel and re-raised as
    :class:`~nw_cli.ui.ReportedError`, so the command's error boundary does not print it again.
    """

    stages: list[Stage] = field(default_factory=list)
    console: Console = field(default_factory=Console)
    title: str = "nw"
    log_path: Path | None = None
    # None: decided by the terminal and --verbose (see nw_cli.ui.stream_output).
    stream: bool | None = None
    _progress: _SpacedProgress | None = None
    _failed: bool = False
    _error: str | None = None
    _results: list[tuple[str, str]] = field(default_factory=list)
    _tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    _log: TextIO | None = None
    _streams: tuple[Any, Any] | None = None
    _fds: _FdCapture | None = None
    _terminal: TextIO | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _started: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.stages:
            self.stages = [
                Stage("connector", "Connector codegen"),
                Stage("wheel", "Wheel build"),
                Stage("mcp", "MCP host build"),
                Stage("wire", "Wire / ALL_PACKAGES"),
            ]

    def mark_skipped(self, key: str) -> None:
        for s in self.stages:
            if s.key == key:
                s.skipped = True
                s.status = StageStatus.SKIPPED
                break

    def result(self, label: str, value: object) -> None:
        """A line for the success panel, e.g. ``("MCP host", path)``."""
        self._results.append((label, str(value)))

    # -- output ---------------------------------------------------------------------------

    def _write_log(self, line: str) -> None:
        with self._lock:  # output() also runs on the descriptor-capture thread
            if self._log is not None:
                self._log.write(f"{time.strftime('%H:%M:%S')} {line}\n")
                self._log.flush()

    def _target(self) -> Console:
        return self._progress.console if self._progress is not None else self.console

    def log(self, message: str = "", *, markup: bool = False) -> None:
        """Print one of ``nw``'s own messages above the live progress bars."""
        self._write_log(Text.from_markup(message).plain if markup else message)
        if markup:
            self._target().print(message)
        else:
            self._target().print(message, markup=False, highlight=False)

    def output(self, line: str) -> None:
        """Record a line of build tool output (see the module docstring)."""
        self._write_log(line)
        self._tail.append(line)
        if self._streaming():
            self._target().print(Text(line, style="dim"), highlight=False)
        elif self._progress is not None and line.strip():
            self._progress.detail = line.strip()

    def _streaming(self) -> bool:
        if self.stream is None:
            # Decided once, while stdout is still the real stream (see _redirect).
            from nw_cli.ui import stream_output

            self.stream = stream_output(self.console)
        return self.stream

    def _redirect(self) -> None:
        if self._fds is not None:
            self._fds.start()
        self._streams = (sys.stdout, sys.stderr)
        sys.stdout = _LineWriter(self.output)  # type: ignore[assignment]
        sys.stderr = _LineWriter(self.output)  # type: ignore[assignment]

    def _restore(self) -> None:
        if self._streams is not None:
            for writer in (sys.stdout, sys.stderr):
                if isinstance(writer, _LineWriter):
                    writer.flush()
            sys.stdout, sys.stderr = self._streams
            self._streams = None
        if self._fds is not None:
            self._fds.stop()

    # -- lifecycle ------------------------------------------------------------------------

    def __enter__(self) -> GenerateProgress:
        if self.log_path is not None:
            self._log = self.log_path.open("w", encoding="utf-8")
            self._write_log(f"{self.title} ({' '.join(sys.argv)})")
        # Bound to the real stream: stdout (and on a terminal fds 1/2) go into output() below.
        target = self.console.file
        if _FdCapture.supported(self.console):
            self._fds = _FdCapture(self.output)  # started by _redirect(), once the bars are up
            self._terminal = target = _FdCapture.open_terminal()
        live_console = Console(
            file=target,
            force_terminal=self.console.is_terminal,
            width=None if self.console.is_terminal else self.console.width,
            color_system=self.console.color_system,  # type: ignore[arg-type]
        )
        self._progress = _SpacedProgress(
            _ActiveSpinnerColumn(style=AMBER),
            TextColumn("[bold]{task.description}"),
            _BlockBarColumn(
                bar_width=None,
                style="grey30",
                complete_style=BLUE,
                finished_style=BLUE,
                pulse_style=AMBER,
            ),
            TimeElapsedColumn(),
            console=live_console,
            expand=True,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self._streaming()
        self._progress.start()
        for s in self.stages:
            if s.skipped:
                tid = self._progress.add_task(self._desc(s), total=1, completed=1)
            else:
                tid = self._progress.add_task(self._desc(s), total=1, completed=0, start=False)
            s.task_id = tid
        self._redirect()
        return self

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Suspend the live bars and give the terminal back (e.g. to ask a question)."""
        live = self._progress
        if live is None:
            yield
            return
        # Erase the bars while paused. A plain stop() leaves the last frame on screen, and after
        # another program has used the terminal (the interactive Claude Code session) start()
        # draws a second copy below it.
        display = live.live
        transient = display.transient
        display.transient = True
        live.stop()
        self._restore()
        try:
            yield
        finally:
            self._redirect()
            display.transient = transient
            live.start()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._restore()
        self._fds = None
        if self._progress is not None:
            running = next((s for s in self.stages if s.status == StageStatus.RUNNING), None)
            if running is not None and exc is not None:
                self._fail(running, exc)
            self._progress.detail = ""
            self._progress.stop()
            self._progress = None
        if self._terminal is not None:
            self._terminal.close()
            self._terminal = None
        try:
            if isinstance(exc, typer.Exit):
                # Already reported (a declined prompt); exit 0 is a deliberate early stop.
                if exc.exit_code == 0:
                    self._print_summary()
                return
            if exc is not None:
                self._failed = True
                if isinstance(exc, KeyboardInterrupt):
                    self._error = "Interrupted."
                elif not any(s.status == StageStatus.FAILED for s in self.stages):
                    self._error = describe(exc)
                self._write_log(f"error: {self._error or describe(exc)}")
            self._print_summary()
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None
        if exc is not None:
            raise ReportedError(exit_code_for(exc)) from exc

    # -- stages ---------------------------------------------------------------------------

    def _desc(self, stage: Stage) -> Text:
        if stage.status == StageStatus.SKIPPED:
            return Text(f"{stage.label} (skipped)", style="dim")
        if stage.status == StageStatus.FAILED:
            return Text(stage.label, style=Style(color=PINK, bold=True))
        if stage.status == StageStatus.DONE:
            return Text(stage.label, style=Style(color=BLUE))
        if stage.status == StageStatus.RUNNING:
            return Text(stage.label, style=Style(color=AMBER, bold=True))
        return Text(stage.label, style="dim")

    def _refresh(self, stage: Stage) -> None:
        assert self._progress is not None and stage.task_id is not None
        completed = (
            1 if stage.status in (StageStatus.DONE, StageStatus.SKIPPED, StageStatus.FAILED) else 0
        )
        self._progress.update(stage.task_id, description=self._desc(stage), completed=completed)
        style = PINK if stage.status == StageStatus.FAILED else BLUE
        for col in self._progress.columns:
            if isinstance(col, BarColumn):
                col.complete_style = style
                col.finished_style = style

    def _fail(self, stage: Stage, exc: BaseException) -> None:
        stage.status = StageStatus.FAILED
        stage.error = "Interrupted." if isinstance(exc, KeyboardInterrupt) else describe(exc)
        stage.elapsed = time.monotonic() - self._started.get(stage.key, time.monotonic())
        self._failed = True
        self._refresh(stage)

    def run_stage(self, key: str, fn: Callable[[], Any]) -> Any:
        """Mark stage running, call *fn*, mark done/failed. Re-raises on failure."""
        stage = next(s for s in self.stages if s.key == key)
        if stage.skipped:
            return None

        assert self._progress is not None and stage.task_id is not None
        stage.status = StageStatus.RUNNING
        self._tail.clear()
        self._progress.detail = ""
        self._started[key] = time.monotonic()
        self._write_log(f"== {stage.label}")
        self._progress.start_task(stage.task_id)
        self._refresh(stage)

        try:
            result = fn()
        except BaseException as exc:
            self._fail(stage, exc)
            raise

        stage.status = StageStatus.DONE
        stage.elapsed = time.monotonic() - self._started[key]
        self._progress.detail = ""
        self._refresh(stage)
        return result

    # -- summary --------------------------------------------------------------------------

    def _stage_table(self) -> Table:
        table = Table.grid(padding=(0, 2))
        table.add_column(width=1)
        table.add_column()
        table.add_column(justify="right", style="dim")
        marks = {
            StageStatus.DONE: Text("✓", style=BLUE),
            StageStatus.FAILED: Text("✗", style=PINK),
            StageStatus.SKIPPED: Text("–", style="dim"),
            StageStatus.PENDING: Text("·", style="dim"),
            StageStatus.RUNNING: Text("·", style="dim"),
        }
        for s in self.stages:
            label = Text(s.label)
            if s.status == StageStatus.SKIPPED:
                label = Text(f"{s.label} (skipped)", style="dim")
            elif s.status in (StageStatus.PENDING, StageStatus.RUNNING):
                label = Text(f"{s.label} (not run)", style="dim")
            elif s.status == StageStatus.FAILED:
                label.stylize(Style(color=PINK, bold=True))
            elapsed = f"{s.elapsed:.1f}s" if s.elapsed is not None else ""
            table.add_row(marks[s.status], label, elapsed)
        return table

    def _print_summary(self) -> None:
        parts: list[RenderableType] = [self._stage_table()]
        if self._failed:
            failed = next((s for s in self.stages if s.status == StageStatus.FAILED), None)
            if failed is not None:
                parts.append(
                    Text.from_markup(
                        f"\nFailed at stage [bold]{escape(failed.label)}[/bold]: "
                        f"{escape(failed.error or '')}"
                    )
                )
                hint = failed.hint if failed.hint is not None else _DEFAULT_HINTS.get(failed.key)
                if self._tail and not self._streaming():
                    parts.append(Text("\nLast output:", style="dim"))
                    parts.append(Text("\n".join(f"  {line}" for line in self._tail), style="dim"))
                if hint:
                    parts.append(Text(f"\n{hint}"))
            else:
                parts.append(Text(f"\n{self._error or 'Failed.'}"))
            border = PINK
        else:
            if self._results:
                results = Table.grid(padding=(0, 2))
                results.add_column(style="bold")
                results.add_column(overflow="fold")
                for label, value in self._results:
                    results.add_row(label, value)
                parts += [Text(""), results]
            border = BLUE
        self.console.print(Panel(Group(*parts), title=self.title, border_style=border, style=TEXT))
        if self.log_path is not None:
            # Outside the panel so the path copies cleanly.
            self.console.print(
                f"Log: {self.log_path}", style="dim", highlight=False, soft_wrap=True
            )
