# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Phase 1 runner: headless /ai-scoping via Claude Code (a fake `claude` binary here)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from nw_cli.scoping import (
    ALLOWED_TOOLS,
    ScopingRequest,
    interactive_prompt,
    link_skill,
    run_ai_scoping,
    run_ai_scoping_interactive,
    scoping_prompt,
)
from nw_cli.stages import StageError

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A node-wire root whose nw-stacklok-builder points at the real vendored skill files."""
    root = tmp_path / "node-wire"
    (root / "nw-stacklok-builder").mkdir(parents=True)
    for sub in ("skills", "agents"):
        (root / "nw-stacklok-builder" / sub).symlink_to(REPO / "nw-stacklok-builder" / sub)
    return root


def _request(tmp_path: Path) -> ScopingRequest:
    return ScopingRequest(
        spec=tmp_path / "spec.openapi.json",
        connector_id="demo",
        workflows=["List pets", "Place orders"],
        auth_hint="bearer token",
    )


def _fake_claude(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "claude"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_prompt_carries_every_answer_and_the_gate_policy(tmp_path: Path) -> None:
    prompt = scoping_prompt(_request(tmp_path), tmp_path / "work")
    assert prompt.startswith(f"/ai-scoping {tmp_path / 'spec.openapi.json'}")
    assert "  - List pets\n  - Place orders" in prompt
    assert "Authentication hint: bearer token" in prompt
    assert f"Working directory: {tmp_path / 'work'}" in prompt
    assert "USER GATE" in prompt and "Auto-approved gates" in prompt


def test_prompt_without_workflows_asks_the_ai_to_propose_them(tmp_path: Path) -> None:
    request = ScopingRequest(spec=tmp_path / "s.json", connector_id="demo", workflows=[])
    prompt = scoping_prompt(request, tmp_path / "work")
    assert "none given. Propose three distinct workflows" in prompt
    assert "Inferred workflows" in prompt


def test_link_skill_is_idempotent(root: Path) -> None:
    link_skill(root)
    link_skill(root)
    skill = root / ".claude" / "skills" / "ai-scoping"
    assert skill.is_symlink() and (skill / "SKILL.md").is_file()
    for agent in ("spec-analyzer", "endpoint-scoper"):
        assert (root / ".claude" / "agents" / f"{agent}.md").is_symlink()


def test_runs_claude_headless_and_returns_the_scope(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    args_file = tmp_path / "args"
    fake = _fake_claude(
        tmp_path,
        f'printf "%s\\n" "$@" > {args_file}\necho "version: \\"1\\"" > {work}/mcp-scope.yaml\n',
    )
    monkeypatch.setenv("NW_CLAUDE_BIN", str(fake))
    lines: list[str] = []

    scope = run_ai_scoping(
        root, _request(tmp_path), work_dir=work, model="claude-opus-5-5", log=lines.append
    )

    assert scope == work / "mcp-scope.yaml" and scope.is_file()
    args = args_file.read_text().splitlines()
    assert args[0] == "-p" and args[1].startswith("/ai-scoping ")
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "Bash(uv run mcp-builder:*)" in args and set(ALLOWED_TOOLS) <= set(args)
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert (root / ".claude" / "skills" / "ai-scoping").is_symlink()


def test_failure_marker_and_missing_scope_are_errors(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "NW_CLAUDE_BIN", str(_fake_claude(tmp_path, 'echo "NW_SCOPING_FAILED: no base URL"\n'))
    )
    with pytest.raises(StageError, match="AI scoping stopped: no base URL"):
        run_ai_scoping(root, _request(tmp_path), work_dir=tmp_path / "w1", log=lambda _: None)

    monkeypatch.setenv("NW_CLAUDE_BIN", str(_fake_claude(tmp_path, "exit 0\n")))
    with pytest.raises(StageError, match="without writing"):
        run_ai_scoping(root, _request(tmp_path), work_dir=tmp_path / "w2", log=lambda _: None)


def test_missing_claude_explains_the_alternative(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NW_CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(StageError, match="--scope"):
        run_ai_scoping(root, _request(tmp_path), work_dir=tmp_path / "w", log=lambda _: None)


def test_interactive_prompt_keeps_the_skills_gates(tmp_path: Path) -> None:
    prompt = interactive_prompt(_request(tmp_path), tmp_path / "work")
    assert prompt.startswith("/ai-scoping ")
    assert "including every USER GATE" in prompt
    assert "confirm or extend them" in prompt and "    - List pets" in prompt
    assert "/exit" in prompt
    assert "do not stop" not in prompt  # no auto-approval in interactive mode


def test_interactive_session_runs_claude_in_the_foreground(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    args_file = tmp_path / "args"
    fake = _fake_claude(
        tmp_path,
        f'printf "%s\\n" "$@" > {args_file}\necho "version: \\"1\\"" > {work}/mcp-scope.yaml\n',
    )
    monkeypatch.setenv("NW_CLAUDE_BIN", str(fake))

    scope = run_ai_scoping_interactive(root, _request(tmp_path), work_dir=work)

    assert scope.is_file()
    args = args_file.read_text().splitlines()
    assert "-p" not in args and args[0].startswith("/ai-scoping ")
    assert "Bash(uv run mcp-builder:*)" in args
    assert "--permission-mode" not in args  # the user answers permission prompts themselves


def test_interactive_session_without_a_scope_is_an_error(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NW_CLAUDE_BIN", str(_fake_claude(tmp_path, "exit 0\n")))
    with pytest.raises(StageError, match="without writing"):
        run_ai_scoping_interactive(root, _request(tmp_path), work_dir=tmp_path / "w")
