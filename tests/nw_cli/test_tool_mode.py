# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``nw gen-all`` / ``nw gen-mcp`` choose the MCP host's tool mode.

The wording and the decision live in nw-mcp-builder (``tool_listing``); nw-cli
only renders the question with Rich — paused progress bars, numbered options
with the full descriptions — and passes flags and the answer through.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nw_cli import tool_mode as tm
from nw_cli.cli import app
from nw_cli.progress import GenerateProgress
from nw_mcp_builder.tool_listing import ToolListing, ToolModeDecision, ToolModePrompt

runner = CliRunner()


def _prompt() -> ToolModePrompt:
    return ToolModePrompt(
        connector_id="slack_web",
        listing=ToolListing(tool_count=174, size_bytes=79_084),
        max_tool_listing_kb=25,
    )


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=120, force_terminal=False, color_system=None), buf


# --- Rich rendering -------------------------------------------------------------


def test_rich_prompt_shows_the_explanation_and_descriptive_options() -> None:
    console, buf = _console()
    assert tm.rich_choose_tool_mode(_prompt(), console=console, stream=io.StringIO("2\n")) == (
        "search"
    )
    out = buf.getvalue()
    assert "174 tools" in out and "77.2 KB" in out
    assert "Where tool search can fail" in out
    assert "Full tool list" in out and "Tool search" in out
    assert "Works with every client and model" in out


def test_rich_prompt_rejects_other_answers_then_accepts() -> None:
    console, _ = _console()
    choice = tm.rich_choose_tool_mode(_prompt(), console=console, stream=io.StringIO("yes\n1\n"))
    assert choice == "list"


def test_rich_prompt_defaults_to_the_full_list_on_eof() -> None:
    console, _ = _console()
    assert tm.rich_choose_tool_mode(_prompt(), console=console, stream=io.StringIO("")) == "list"


# --- Decision wiring -------------------------------------------------------------


def _capture_resolve(monkeypatch: pytest.MonkeyPatch, mode: str = "list") -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_resolve(connector_id: str, root: Path, **kwargs: Any) -> ToolModeDecision:
        seen.update(kwargs, connector_id=connector_id)
        return ToolModeDecision(mode=mode, listing=ToolListing(174, 79_084))

    monkeypatch.setattr(tm, "resolve_tool_mode", fake_resolve)
    return seen


def test_no_terminal_means_no_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen = _capture_resolve(monkeypatch)
    monkeypatch.setattr(tm, "is_interactive", lambda: False)
    console, _ = _console()
    tm.decide_tool_mode(tmp_path, "slack_web", console=console, notify=lambda m: None)
    assert seen["choose"] is None


def test_terminal_prompt_runs_with_progress_paused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_resolve(monkeypatch)
    monkeypatch.setattr(tm, "is_interactive", lambda: True)
    events: list[str] = []

    class _Pause:
        def __enter__(self) -> None:
            events.append("paused")

        def __exit__(self, *exc: Any) -> None:
            events.append("resumed")

    monkeypatch.setattr(
        tm, "rich_choose_tool_mode", lambda p, **k: events.append("asked") or "search"
    )
    console, _ = _console()
    tm.decide_tool_mode(tmp_path, "slack_web", console=console, notify=lambda m: None, pause=_Pause)
    assert seen["choose"](_prompt()) == "search"
    assert events == ["paused", "asked", "resumed"]


def test_flags_and_budget_reach_the_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_resolve(monkeypatch, mode="search")
    console, _ = _console()
    decision = tm.decide_tool_mode(
        tmp_path,
        "slack_web",
        tool_mode="search",
        max_tool_listing_kb=40,
        console=console,
        notify=lambda m: None,
    )
    assert decision.mode == "search"
    assert seen["tool_mode"] == "search" and seen["max_tool_listing_kb"] == 40


def test_progress_can_pause_and_resume() -> None:
    console, _ = _console()
    progress = GenerateProgress(console=console)
    with progress:
        with progress.paused():
            assert progress._progress is not None
            assert not progress._progress.live.is_started
        assert progress._progress.live.is_started


# --- CLI commands ------------------------------------------------------------------


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "build-packages.sh").write_text("#!/bin/bash\nALL_PACKAGES=(\n)\n")
    (tmp_path / "nw-mcp-builder").mkdir()
    for rel in (
        "packages/runtime/dist/runtime.whl",
        "packages/bindings/dist/bindings.whl",
        "packages/connectors/pet_store/dist/c.whl",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return tmp_path


def test_gen_all_decides_after_codegen_and_passes_the_mode(fake_root: Path) -> None:
    order: list[str] = []

    def fake_decide(root: Path, connector_id: str, **kwargs: Any) -> ToolModeDecision:
        order.append("decide")
        assert kwargs["tool_mode"] == "search"
        assert kwargs["max_tool_listing_kb"] == 30
        return ToolModeDecision(mode="search", listing=ToolListing(174, 79_084))

    def fake_mcp(root: Path, cid: str, **kwargs: Any) -> Path:
        order.append("mcp")
        assert kwargs["tool_mode"] == "search"
        return root

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch(
            "nw_connector_builder.pipeline.run_build",
            side_effect=lambda **k: order.append("connector") or 0,
        ),
        patch("nw_cli.cli.decide_tool_mode", side_effect=fake_decide),
        patch("nw_cli.cli.run_wheel_build", side_effect=lambda *a, **k: order.append("wheel")),
        patch("nw_cli.cli.run_mcp_build", side_effect=fake_mcp),
        patch("nw_cli.cli.register_all_packages", return_value=True),
    ):
        result = runner.invoke(
            app,
            [
                "gen-all",
                "--connector-id",
                "pet_store",
                "--path",
                "spec.yaml",
                "--tool-search",
                "--max-tool-listing-kb",
                "30",
            ],
        )
    assert result.exit_code == 0, result.output
    assert order == ["connector", "decide", "wheel", "wheel", "mcp"]


def test_gen_all_rejects_both_mode_flags(fake_root: Path) -> None:
    with patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root):
        result = runner.invoke(
            app,
            [
                "gen-all",
                "--connector-id",
                "pet_store",
                "--path",
                "spec.yaml",
                "--tool-search",
                "--full-tool-list",
            ],
        )
    assert result.exit_code != 0
    assert "--tool-search" in result.output and "--full-tool-list" in result.output


def test_gen_all_skips_the_decision_without_an_mcp_stage(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build", return_value=0),
        patch("nw_cli.cli.decide_tool_mode") as decide,
        patch("nw_cli.cli.run_wheel_build"),
        patch("nw_cli.cli.register_all_packages", return_value=True),
    ):
        result = runner.invoke(
            app,
            ["gen-all", "--connector-id", "pet_store", "--path", "spec.yaml", "--no-mcp"],
        )
    assert result.exit_code == 0, result.output
    decide.assert_not_called()


def test_gen_mcp_passes_the_mode(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch(
            "nw_cli.cli.decide_tool_mode",
            return_value=ToolModeDecision(mode="list", listing=None),
        ) as decide,
        patch("nw_cli.cli.run_mcp_build", return_value=fake_root) as mcp,
    ):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store", "--full-tool-list"])
    assert result.exit_code == 0, result.output
    assert decide.call_args.kwargs["tool_mode"] == "list"
    mcp.assert_called_once_with(fake_root, "pet_store", force_output=False, tool_mode="list")
