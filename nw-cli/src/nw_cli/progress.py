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

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.style import Style
from rich.text import Text

# Node Wire brand palette (docs/stylesheets/extra.css)
AMBER = "#ecb32e"
BLUE = "#37c4f0"
PINK = "#e01d5a"
TEXT = "#E8EDF5"


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
        self._progress = Progress(
            SpinnerColumn(style=AMBER),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=None, complete_style=BLUE, finished_style=BLUE),
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
        completed = 1 if stage.status in (
            StageStatus.DONE,
            StageStatus.SKIPPED,
            StageStatus.FAILED,
        ) else 0
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
                    "wheel": "Fix with: nw wheel --id <id>  (or nw wheel --runtime)",
                    "mcp": "Ensure wheels exist, then: nw mcp --id <id>",
                    "wire": "Check scripts/build-packages.sh ALL_PACKAGES block.",
                }
                hint = f"\n{hints.get(failed.key, '')}"
                msg = f"Failed at stage [bold]{failed.label}[/bold]: {failed.error}{hint}"
            else:
                msg = "Generate failed."
            self.console.print(
                Panel(msg, title="nw generate", border_style=PINK, style=TEXT)
            )
        else:
            done = [s.label for s in self.stages if s.status == StageStatus.DONE]
            skipped = [s.label for s in self.stages if s.status == StageStatus.SKIPPED]
            parts = []
            if done:
                parts.append(f"Completed: {', '.join(done)}")
            if skipped:
                parts.append(f"Skipped: {', '.join(skipped)}")
            body = "\n".join(parts) if parts else "Done."
            self.console.print(
                Panel(body, title="nw generate", border_style=BLUE, style=TEXT)
            )
