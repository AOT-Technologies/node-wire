#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""nw-mcp-builder sizes a connector's tool listing and bakes the tool mode into the host.

A listing over the budget (25 KB by default) never fails the build. On a terminal
the user is asked — with descriptive options — whether to generate the full tool
list or use tool search; without one, the full list is generated and a warning
names the flags that choose explicitly.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from nw_mcp_builder import cli
from nw_mcp_builder.from_connector import action_to_tool_name, run_from_connector
from nw_mcp_builder.schema.models import Tool as ScopeTool
from nw_mcp_builder.tool_listing import (
    DEFAULT_MAX_TOOL_LISTING_KB,
    ToolListing,
    ToolModePrompt,
    measure_tool_listing,
    prompt_on_terminal,
    resolve_tool_mode,
)

_TINY_BUDGET_KB = 0.01  # the two-tool demo connector is well over this


def test_default_budget_is_25_kb() -> None:
    assert DEFAULT_MAX_TOOL_LISTING_KB == 25


def test_listing_is_measured_from_the_connector_source(fake_node_wire: Path) -> None:
    listing = measure_tool_listing("demo_conn", fake_node_wire)
    assert listing.tool_count == 2
    assert listing.size_bytes > 0


def test_explicit_mode_wins_without_asking(fake_node_wire: Path) -> None:
    def never(prompt: ToolModePrompt) -> str:
        raise AssertionError("must not ask when a mode was chosen")

    decision = resolve_tool_mode(
        "demo_conn",
        fake_node_wire,
        tool_mode="search",
        max_tool_listing_kb=_TINY_BUDGET_KB,
        choose=never,
    )
    assert decision.mode == "search"


def test_under_budget_uses_the_full_list_silently(fake_node_wire: Path) -> None:
    notices: list[str] = []
    decision = resolve_tool_mode(
        "demo_conn", fake_node_wire, choose=lambda p: "search", notify=notices.append
    )
    assert decision.mode == "list"
    assert notices == []


def test_over_budget_asks_and_uses_the_answer(fake_node_wire: Path) -> None:
    asked: list[ToolModePrompt] = []

    def choose(prompt: ToolModePrompt) -> str:
        asked.append(prompt)
        return "search"

    decision = resolve_tool_mode(
        "demo_conn", fake_node_wire, max_tool_listing_kb=_TINY_BUDGET_KB, choose=choose
    )
    assert decision.mode == "search"
    assert asked and asked[0].listing.tool_count == 2


def test_over_budget_without_a_terminal_generates_the_full_list_and_warns(
    fake_node_wire: Path,
) -> None:
    notices: list[str] = []
    decision = resolve_tool_mode(
        "demo_conn", fake_node_wire, max_tool_listing_kb=_TINY_BUDGET_KB, notify=notices.append
    )
    assert decision.mode == "list"
    (warning,) = notices
    assert "over the" in warning
    assert "--tool-search" in warning and "--full-tool-list" in warning


def test_measurement_failure_falls_back_to_the_full_list(tmp_path: Path) -> None:
    notices: list[str] = []
    decision = resolve_tool_mode("missing_conn", tmp_path, notify=notices.append)
    assert decision.mode == "list"
    assert decision.listing is None
    assert "Could not measure" in notices[0]


def test_unknown_mode_is_rejected(fake_node_wire: Path) -> None:
    with pytest.raises(ValueError, match="tool mode"):
        resolve_tool_mode("demo_conn", fake_node_wire, tool_mode="lazy")


def _prompt() -> ToolModePrompt:
    return ToolModePrompt(
        connector_id="slack_web",
        listing=ToolListing(tool_count=174, size_bytes=80_998),
        max_tool_listing_kb=25,
    )


def test_prompt_explains_tool_search_and_where_it_fails() -> None:
    text = _prompt().render_text()
    assert "174 tools" in text and "79.1 KB" in text and "25 KB" in text
    assert "nw_search_tools" in text and "nw_call_tool" in text
    assert "Where tool search can fail" in text
    assert "guesses a tool name" in text
    assert "approve or filter tools one by one" in text


def test_options_are_descriptive_not_yes_no() -> None:
    options = _prompt().options
    assert [o.mode for o in options] == ["list", "search"]
    assert options[0].label.startswith("Full tool list")
    assert options[1].label.startswith("Tool search")
    assert all(len(o.detail) > 40 for o in options)


def test_terminal_prompt_takes_a_numbered_choice() -> None:
    out = io.StringIO()
    answers = iter(["x", "2"])
    asked: list[str] = []

    def answer(question: str) -> str:
        asked.append(question)
        return next(answers)

    assert prompt_on_terminal(_prompt(), input_fn=answer, output=out) == "search"
    assert asked == ["Choose 1 or 2: ", "Choose 1 or 2: "]
    assert "Please enter a number from 1 to 2." in out.getvalue()


def test_terminal_prompt_defaults_to_the_full_list_on_eof() -> None:
    def eof(_: str) -> str:
        raise EOFError

    assert prompt_on_terminal(_prompt(), input_fn=eof, output=io.StringIO()) == "list"


# --- The mode reaches the generated host -------------------------------------


def _host(project_dir: Path) -> tuple[str, str]:
    main = (project_dir / "src" / "demo_conn_nw_mcp" / "__main__.py").read_text(encoding="utf-8")
    dockerfile = (project_dir / "Dockerfile").read_text(encoding="utf-8")
    return main, dockerfile


def test_search_mode_is_baked_into_the_host(fake_node_wire: Path, package_root: Path) -> None:
    project = run_from_connector(
        "demo_conn",
        node_wire_root=fake_node_wire,
        package_root=package_root,
        skip_build_wheels=True,
        tool_mode="search",
    )
    main, dockerfile = _host(project)
    assert 'os.environ.setdefault("NW_MCP_TOOL_MODE", "search")' in main
    assert "NW_MCP_TOOL_MODE=search" in dockerfile
    assert "NW_MCP_TOOL_MODE" in (project / "README.md").read_text(encoding="utf-8")


def test_small_connector_gets_the_full_list(fake_node_wire: Path, package_root: Path) -> None:
    project = run_from_connector(
        "demo_conn",
        node_wire_root=fake_node_wire,
        package_root=package_root,
        skip_build_wheels=True,
    )
    main, dockerfile = _host(project)
    assert 'os.environ.setdefault("NW_MCP_TOOL_MODE", "list")' in main
    assert "NW_MCP_TOOL_MODE=list" in dockerfile


def test_over_budget_choice_is_made_before_wheels_are_built(
    fake_node_wire: Path, package_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    import nw_mcp_builder.from_connector as fc

    monkeypatch.setattr(fc, "build_connector_wheels", lambda *a, **k: order.append("wheels"))

    def choose(prompt: ToolModePrompt) -> str:
        order.append("asked")
        return "search"

    project = run_from_connector(
        "demo_conn",
        node_wire_root=fake_node_wire,
        package_root=package_root,
        max_tool_listing_kb=_TINY_BUDGET_KB,
        choose_tool_mode=choose,
    )
    assert order == ["asked", "wheels"]
    assert "NW_MCP_TOOL_MODE=search" in _host(project)[1]


# --- CLI ------------------------------------------------------------------------


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    cli.add_mcp_arguments(parser)
    return parser.parse_args(argv)


def test_cli_mode_flags_are_mutually_exclusive() -> None:
    assert _parse(["-c", "x", "--tool-search"]).tool_mode == "search"
    assert _parse(["-c", "x", "--full-tool-list"]).tool_mode == "list"
    assert _parse(["-c", "x"]).tool_mode is None
    with pytest.raises(SystemExit):
        _parse(["-c", "x", "--tool-search", "--full-tool-list"])


def test_cli_passes_mode_and_budget_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(connector_id: str, **kwargs: Any) -> Path:
        seen.update(kwargs)
        return Path("/tmp/out")

    monkeypatch.setattr(cli, "run_from_connector", fake_run)
    monkeypatch.setattr(cli, "format_success_message", lambda *a: "ok")
    cli.run_mcp_from_args(_parse(["-c", "x", "--tool-search", "--max-tool-listing-kb", "40"]))
    assert seen["tool_mode"] == "search"
    assert seen["max_tool_listing_kb"] == 40.0


def test_cli_does_not_prompt_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "run_from_connector", lambda cid, **kw: seen.update(kw) or Path("/"))
    monkeypatch.setattr(cli, "format_success_message", lambda *a: "ok")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    cli.run_mcp_from_args(_parse(["-c", "x"]))
    assert seen["choose_tool_mode"] is None


# --- Fixture tool names follow the MCP limit, not a 40-char cut -----------------


def test_fixture_tool_names_are_not_cut_at_40() -> None:
    name = "admin_conversations_restrict_access_remove_group"  # 48 chars
    assert action_to_tool_name(name) == name


def test_fixture_schema_accepts_names_up_to_64_characters() -> None:
    ScopeTool(tool_name="a" * 64, endpoint="POST /x", description="d", response_kind="json")
    with pytest.raises(ValueError, match="64"):
        ScopeTool(tool_name="a" * 65, endpoint="POST /x", description="d", response_kind="json")
