# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Choose the generated MCP host's tool mode (full list vs tool search).

The decision and its wording belong to nw-mcp-builder
(:mod:`nw_mcp_builder.tool_listing`); this module only renders the question
with Rich and wires flags, terminal detection and the progress display to it.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import TextIO

from nw_mcp_builder.tool_listing import (
    DEFAULT_MAX_TOOL_LISTING_KB,
    ToolModeDecision,
    ToolModePrompt,
    resolve_tool_mode,
)
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nw_cli.prerequisites import is_interactive
from nw_cli.ui import AMBER, BLUE

Pause = Callable[[], AbstractContextManager[object]]


def rich_choose_tool_mode(
    prompt: ToolModePrompt, *, console: Console, stream: TextIO | None = None
) -> str:
    """Show the over-budget explanation and numbered options; return the chosen mode.

    ``stream`` replaces the terminal for tests. End of input picks the full
    list, the same default as a build without a terminal.
    """
    console.print(
        Panel(
            Text(prompt.explanation()),
            title="Large tool listing",
            title_align="left",
            border_style=AMBER,
        )
    )
    console.print(f"How should [bold]{prompt.connector_id}[/bold] expose its tools?")
    for number, option in enumerate(prompt.options, start=1):
        console.print(f"  [bold {BLUE}]{number}[/]  [bold]{option.label}[/bold]", highlight=False)
        console.print(f"     [dim]{option.detail}[/dim]", highlight=False)

    count = len(prompt.options)
    question = f"Choose 1 or {count}: "
    while True:
        try:
            if stream is None:
                answer = console.input(question)
            else:
                console.print(question, end="")
                raw = stream.readline()
                if not raw:
                    raise EOFError
                answer = raw
        except EOFError:
            console.print()
            return "list"
        answer = answer.strip()
        if answer.isdigit() and 1 <= int(answer) <= count:
            return prompt.options[int(answer) - 1].mode
        console.print(f"[dim]Please enter a number from 1 to {count}.[/dim]")


def decide_tool_mode(
    node_wire_root: Path,
    connector_id: str,
    *,
    tool_mode: str | None = None,
    max_tool_listing_kb: float = DEFAULT_MAX_TOOL_LISTING_KB,
    console: Console,
    notify: Callable[[str], None],
    pause: Pause = nullcontext,
) -> ToolModeDecision:
    """Measure, and ask on a terminal when over budget. ``pause`` suspends live output."""
    choose = None
    if tool_mode is None and is_interactive():

        def choose(prompt: ToolModePrompt) -> str:
            with pause():
                return rich_choose_tool_mode(prompt, console=console)

    return resolve_tool_mode(
        connector_id,
        node_wire_root,
        tool_mode=tool_mode,
        max_tool_listing_kb=max_tool_listing_kb,
        choose=choose,
        notify=notify,
    )


def tool_mode_from_flags(tool_search: bool, full_tool_list: bool) -> str | None:
    """``--tool-search`` / ``--full-tool-list`` → mode, or None to decide by size."""
    if tool_search and full_tool_list:
        raise ValueError("Use either --tool-search or --full-tool-list, not both.")
    if tool_search:
        return "search"
    if full_tool_list:
        return "list"
    return None


def describe_decision(decision: ToolModeDecision) -> str:
    """One line for the build log: the mode and the measured listing."""
    label = "tool search" if decision.mode == "search" else "full tool list"
    if decision.listing is None:
        return f"Tool mode: {label}"
    return (
        f"Tool mode: {label} (listing {decision.listing.kb:.1f} KB, "
        f"{decision.listing.tool_count} tools)"
    )
