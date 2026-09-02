# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Brand-styled rich.progress output for multi-stage ``nw generate``.

Logs are always printed *above* the live progress bars via the Progress
console (and stdout/stderr redirection). Subprocess output must be fed
through :meth:`GenerateProgress.log` — never written to the raw TTY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from rich.console import Console, ConsoleOptions, RenderResult
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
from rich.text import Text

# Node Wire brand palette (docs/stylesheets/extra.css)
AMBER = "#ecb32e"
BLUE = "#37c4f0"
PINK = "#e01d5a"
TEXT = "#E8EDF5"


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


class _SpacedProgress(Progress):
    """Progress display with a blank line between task rows."""

    def make_tasks_table(self, tasks):  # type: ignore[override]
        table = super().make_tasks_table(tasks)
        # A bottom pad of 1 (without collapsing) puts a blank line under each
        # row; pad_edge=False keeps it between rows only, not after the last.
        table.padding = (0, 1, 1, 0)
        table.collapse_padding = False
        table.pad_edge = False
        return table


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


@dataclass
class GenerateProgress:
    """Drive a four-stage progress display for ``nw generate``."""

    stages: list[Stage] = field(default_factory=list)
    console: Console = field(default_factory=Console)
    _progress: Progress | None = None
    _failed: bool = False

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

    def log(self, message: str = "", *, markup: bool = False) -> None:
        """Print a log line above the live progress bars."""
        # Progress.console.print goes through Live and stays above the bars.
        target = self._progress.console if self._progress is not None else self.console
        if markup:
            target.print(message)
        else:
            target.print(message, markup=False, highlight=False)

    def __enter__(self) -> GenerateProgress:
        self._progress = _SpacedProgress(
            SpinnerColumn(style=AMBER),
            TextColumn("[bold]{task.description}"),
            _BlockBarColumn(
                bar_width=None,
                style="grey30",
                complete_style=BLUE,
                finished_style=BLUE,
                pulse_style=AMBER,
            ),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
            # Route Python print()/logging through Live so they stay above bars.
            redirect_stdout=True,
            redirect_stderr=True,
        )
        self._progress.start()
        for s in self.stages:
            if s.skipped:
                tid = self._progress.add_task(
                    self._desc(s),
                    total=1,
                    completed=1,
                )
            else:
                tid = self._progress.add_task(
                    self._desc(s),
                    total=1,
                    completed=0,
                    start=False,
                )
            s.task_id = tid
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        self._print_summary()

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
            1
            if stage.status
            in (
                StageStatus.DONE,
                StageStatus.SKIPPED,
                StageStatus.FAILED,
            )
            else 0
        )
        self._progress.update(
            stage.task_id,
            description=self._desc(stage),
            completed=completed,
        )
        if stage.status == StageStatus.FAILED:
            for col in self._progress.columns:
                if isinstance(col, BarColumn):
                    col.complete_style = PINK
                    col.finished_style = PINK

    def run_stage(self, key: str, fn: Callable[[], Any]) -> Any:
        """Mark stage running, call *fn*, mark done/failed. Re-raises on failure."""
        stage = next(s for s in self.stages if s.key == key)
        if stage.skipped:
            return None

        assert self._progress is not None and stage.task_id is not None
        stage.status = StageStatus.RUNNING
        self._progress.start_task(stage.task_id)
        self._refresh(stage)

        try:
            result = fn()
        except Exception as exc:
            stage.status = StageStatus.FAILED
            stage.error = str(exc)
            self._failed = True
            self._refresh(stage)
            raise

        stage.status = StageStatus.DONE
        self._refresh(stage)
        if self._progress is not None:
            for col in self._progress.columns:
                if isinstance(col, BarColumn):
                    col.complete_style = BLUE
                    col.finished_style = BLUE
        return result

    def _print_summary(self) -> None:
        if self._failed:
            failed = next((s for s in self.stages if s.status == StageStatus.FAILED), None)
            hint = ""
            if failed is not None:
                hints = {
                    "connector": "Check the OpenAPI spec path and connector id.",
                    "wheel": "Fix with: nw gen-whl --connector-id <id>  (or nw gen-whl --runtime)",
                    "mcp": "Ensure wheels exist, then: nw gen-mcp --connector-id <id>",
                    "wire": "Check scripts/build-packages.sh ALL_PACKAGES block.",
                }
                hint = f"\n{hints.get(failed.key, '')}"
                msg = f"Failed at stage [bold]{failed.label}[/bold]: {failed.error}{hint}"
            else:
                msg = "Generate failed."
            self.console.print(Panel(msg, title="nw generate", border_style=PINK, style=TEXT))
        else:
            done = [s.label for s in self.stages if s.status == StageStatus.DONE]
            skipped = [s.label for s in self.stages if s.status == StageStatus.SKIPPED]
            parts = []
            if done:
                parts.append(f"Completed: {', '.join(done)}")
            if skipped:
                parts.append(f"Skipped: {', '.join(skipped)}")
            body = "\n".join(parts) if parts else "Done."
            self.console.print(Panel(body, title="nw generate", border_style=BLUE, style=TEXT))
