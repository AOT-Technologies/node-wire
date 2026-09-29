# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Size a connector's MCP tool listing and decide the host's tool mode.

MCP clients send the whole ``tools/list`` result to the model with every
request. When a connector's listing is over the budget, the host can be
generated in **tool-search mode** instead: the server lists ``nw_search_tools``
and ``nw_call_tool`` and hands out individual tool schemas on demand (see the
bindings' ``NW_MCP_TOOL_MODE``).

Over budget never fails the build. With a ``choose`` callback (a terminal) the
user picks; without one the full list is generated and ``notify`` receives a
warning naming the flags that choose explicitly.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_LISTING_KB = 25.0
TOOL_MODES = ("list", "search")


@dataclass(frozen=True)
class ToolListing:
    """A connector's full tool listing as the MCP server would send it."""

    tool_count: int
    size_bytes: int

    @property
    def kb(self) -> float:
        return self.size_bytes / 1024


@dataclass(frozen=True)
class ToolModeOption:
    mode: str
    label: str
    detail: str


@dataclass(frozen=True)
class ToolModePrompt:
    """What the user is shown when a listing is over budget. Wording lives here only."""

    connector_id: str
    listing: ToolListing
    max_tool_listing_kb: float
    options: tuple[ToolModeOption, ...] = field(init=False)

    def __post_init__(self) -> None:
        size = f"{self.listing.kb:.1f} KB"
        object.__setattr__(
            self,
            "options",
            (
                ToolModeOption(
                    mode="list",
                    label=f"Full tool list — all {self.listing.tool_count} tools listed "
                    f"individually ({size})",
                    detail=f"Works with every client and model; costs {size} of context "
                    "on every request.",
                ),
                ToolModeOption(
                    mode="search",
                    label="Tool search — 2 tools listed (~2 KB); the model searches, then calls",
                    detail="Best for large connectors and capable models; see where it can "
                    "fail above.",
                ),
            ),
        )

    def explanation(self) -> str:
        return (
            f"{self.connector_id} will publish {self.listing.tool_count} tools in a "
            f"{self.listing.kb:.1f} KB tool listing (budget {self.max_tool_listing_kb:g} KB).\n"
            "MCP clients send the whole listing to the model with every request, so a listing "
            "this large\nuses context on every turn and can push smaller models past their "
            "limits.\n\n"
            "Tool search replaces the listing with two tools: the model calls nw_search_tools "
            "with a few\nkeywords, gets the matching tools and their arguments, then runs one "
            "with nw_call_tool.\n\n"
            "Where tool search can fail:\n"
            "  • the model skips searching and guesses a tool name, or gives up after one search\n"
            "  • its words don't match the tool's name or description "
            '("DM someone" vs "conversations_open")\n'
            "  • clients that show, approve or filter tools one by one only ever see "
            "nw_call_tool\n"
            "  • every task takes an extra round trip"
        )

    def render_text(self) -> str:
        lines = [self.explanation(), "", f"How should {self.connector_id} expose its tools?"]
        for number, option in enumerate(self.options, start=1):
            lines.append(f"  {number}  {option.label}")
            lines.append(f"     {option.detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ToolModeDecision:
    mode: str
    listing: ToolListing | None


ChooseToolMode = Callable[[ToolModePrompt], str]
Notify = Callable[[str], None]


def measure_tool_listing(connector_id: str, node_wire_root: Path) -> ToolListing:
    """Measure ``connector_id``'s full listing from its source under ``node_wire_root``.

    Uses the bindings' own definition of an advertised tool and its serialized
    size, so the number is what the generated host would send. Measured without
    multitenancy (which adds a ``config_name`` argument to every tool).
    """
    # Imported before the connector's src/ tree goes on sys.path, so a
    # same-named package in that tree cannot shadow the installed bindings.
    from bindings.mcp_server.server import advertised_tools_for_connectors, tool_listing_bytes

    from nw_mcp_builder.connector_class import load_connector_class

    logic = node_wire_root / "src" / f"node_wire_{connector_id}" / "logic.py"
    if not logic.is_file():
        raise FileNotFoundError(f"Connector logic.py missing: {logic}")
    tools = advertised_tools_for_connectors([load_connector_class(logic)], multitenancy=False)
    return ToolListing(tool_count=len(tools), size_bytes=tool_listing_bytes(tools))


def resolve_tool_mode(
    connector_id: str,
    node_wire_root: Path,
    *,
    tool_mode: str | None = None,
    max_tool_listing_kb: float = DEFAULT_MAX_TOOL_LISTING_KB,
    choose: ChooseToolMode | None = None,
    notify: Notify | None = None,
) -> ToolModeDecision:
    """Pick ``list`` or ``search`` for the generated host. Never fails the build."""
    if tool_mode is not None and tool_mode not in TOOL_MODES:
        raise ValueError(f"Unknown tool mode {tool_mode!r}: expected one of {list(TOOL_MODES)}")
    say = notify or logger.warning

    try:
        listing: ToolListing | None = measure_tool_listing(connector_id, node_wire_root)
    except Exception as exc:  # noqa: BLE001 — sizing is advisory; never block the build
        listing = None
        if tool_mode is None:
            say(
                f"Could not measure the {connector_id} tool listing ({exc}); generating the "
                "full tool list. Pass --tool-search or --full-tool-list to choose explicitly."
            )
    if tool_mode is not None:
        return ToolModeDecision(mode=tool_mode, listing=listing)
    if listing is None or listing.kb <= max_tool_listing_kb:
        return ToolModeDecision(mode="list", listing=listing)

    prompt = ToolModePrompt(
        connector_id=connector_id, listing=listing, max_tool_listing_kb=max_tool_listing_kb
    )
    if choose is not None:
        mode = choose(prompt)
        if mode not in TOOL_MODES:
            raise ValueError(f"Unknown tool mode {mode!r}: expected one of {list(TOOL_MODES)}")
        return ToolModeDecision(mode=mode, listing=listing)

    say(
        f"{connector_id}: tool listing is {listing.kb:.1f} KB for {listing.tool_count} tools, "
        f"over the {max_tool_listing_kb:g} KB budget. Generating the full tool list (no "
        "terminal to ask on). Rebuild with --tool-search to serve tools through search, or "
        "pass --full-tool-list to keep the full list without this warning."
    )
    return ToolModeDecision(mode="list", listing=listing)


def prompt_on_terminal(
    prompt: ToolModePrompt,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO | None = None,
) -> str:
    """Plain-text chooser: show the prompt, read ``1`` or ``2``. EOF picks the full list."""
    out = output or sys.stdout
    print(prompt.render_text(), file=out)
    count = len(prompt.options)
    while True:
        try:
            answer = input_fn(f"Choose 1 or {count}: ").strip()
        except EOFError:
            return "list"
        if answer.isdigit() and 1 <= int(answer) <= count:
            return prompt.options[int(answer) - 1].mode
        print(f"Please enter a number from 1 to {count}.", file=out)


def terminal_chooser() -> ChooseToolMode | None:
    """``prompt_on_terminal`` when stdin and stdout are a terminal, else None."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return prompt_on_terminal
    return None
