# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Phase 2 review of ``nw gen-stacklok``: one screen, then generate / edit / redo / stop."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from rich.console import Console

from nw_cli.progress import GenerateProgress, Stage
from nw_cli.review import ReviewDecision, _editor, attention_items, review_scope
from nw_cli.stacklok import ScopeCheck
from nw_cli.stages import StageError

FIXTURES = Path(__file__).resolve().parents[1] / "nw_stacklok_builder" / "fixtures" / "stacklok"
OK = ScopeCheck(errors=[], warnings=["w1"])
BROKEN = ScopeCheck(errors=["tool 'x' maps to an unknown endpoint"], warnings=[])


@pytest.fixture
def scope_file(tmp_path: Path) -> Path:
    path = tmp_path / "scoping" / "mcp-scope.yaml"
    path.parent.mkdir(parents=True)
    path.write_text((FIXTURES / "petstore.yaml").read_text(), encoding="utf-8")
    (path.parent / "scoping-summary.md").write_text(
        "# Summary\n\n## Flagged for Phase 2 Review\n\n- [ ] Check the `api_key` header\n"
        "- [ ] Pagination returns one page\n\n## Other\n\n- not for the reviewer\n",
        encoding="utf-8",
    )
    return path


def _review(
    tmp_path: Path,
    scope_file: Path,
    answers: list[str],
    *,
    checks: list[ScopeCheck] | None = None,
    interactive: bool = True,
    rescope: Any = None,
    force: bool = False,
) -> tuple[ReviewDecision, Path, str, list[dict[str, Any]]]:
    out = io.StringIO()
    progress = GenerateProgress(
        stages=[Stage("review", "Review")],
        console=Console(file=out, force_terminal=False, width=120),
        log_path=tmp_path / "run.log",
    )
    asked: list[dict[str, Any]] = []
    replies = iter(answers)

    def ask(*_args: Any, **kwargs: Any) -> str:  # the free-text feedback question
        return next(replies)

    def pick(_console: Any, _title: str, options: Any, **_kw: Any) -> str:
        asked.append({"choices": [o.key for o in options], "options": options})
        return next(replies)

    verdicts = iter(checks or [OK] * 10)
    with (
        patch("nw_cli.review.is_interactive", return_value=interactive),
        patch("nw_cli.review.Prompt.ask", side_effect=ask),
        patch("nw_cli.review.choose", side_effect=pick),
        patch(
            "nw_cli.review.validate_stacklok_scope",
            side_effect=lambda *a: (next(verdicts), None, None),
        ),
    ):
        with progress:
            decision, final = review_scope(
                progress,
                node_wire_root=tmp_path,
                scope_file=scope_file,
                connector_id="pet_store",
                summary=scope_file.parent / "scoping-summary.md",
                output_dir=tmp_path / "out",
                force=force,
                reused_at=None,
                rescope=rescope or (lambda feedback: scope_file),
            )
    return decision, final, out.getvalue(), asked


def test_the_summary_bullets_for_the_reviewer_are_extracted(scope_file: Path) -> None:
    items = attention_items(scope_file.parent / "scoping-summary.md")
    assert items == {
        "Flagged for Phase 2 Review": ["Check the `api_key` header", "Pagination returns one page"]
    }


def test_the_summary_is_short_and_details_are_on_request(tmp_path: Path, scope_file: Path) -> None:
    decision, _, text, asked = _review(tmp_path, scope_file, ["d", "g"])
    assert decision == ReviewDecision(generate=True, replace_output=False)
    summary, details = text.split("Scope details", 1)
    assert "5 tools" in summary and "find_pets_by_status" in summary
    assert "Passes stacklok validation (1 warnings" in summary
    assert "2 point(s) flagged" in summary
    assert "GET /pet/findByStatus" not in summary and "Pagination" not in summary
    assert "GET /pet/findByStatus" in details and "Pagination returns one page" in details
    assert "not for the reviewer" not in text
    assert asked[0]["choices"] == ["g", "d", "e", "r", "s"]
    assert len(asked) == 2  # details, then the menu again
    assert "scope warning: w1" in (tmp_path / "run.log").read_text()


def test_an_invalid_scope_cannot_be_generated(tmp_path: Path, scope_file: Path) -> None:
    decision, _, text, asked = _review(tmp_path, scope_file, ["s"], checks=[BROKEN])
    assert not decision.generate
    assert "unknown endpoint" in text
    assert asked[0]["choices"] == ["d", "e", "r", "s"]  # no generate


def test_replacing_existing_output_needs_an_explicit_choice(
    tmp_path: Path, scope_file: Path
) -> None:
    server = yaml.safe_load(scope_file.read_text())["server"]["name"]
    (tmp_path / "out" / f"{server}-mcp").mkdir(parents=True)
    decision, _, text, asked = _review(tmp_path, scope_file, ["g"])
    assert decision == ReviewDecision(generate=True, replace_output=True)
    assert "replaces" in asked[0]["options"][0].hint
    assert "(replaces the existing project)" in text


def test_edit_then_review_again(
    tmp_path: Path, scope_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = tmp_path / "editor.py"
    editor.write_text(
        "import sys, pathlib\np = pathlib.Path(sys.argv[1])\n"
        "p.write_text(p.read_text() + '\\n# edited\\n')\n"
    )
    monkeypatch.setenv("VISUAL", f"{sys.executable} {editor}")
    decision, _, text, asked = _review(tmp_path, scope_file, ["e", "g"], checks=[BROKEN, OK])
    assert decision.generate
    assert scope_file.read_text().endswith("# edited\n")
    assert len(asked) == 2 and text.count("Phase 2: review the scope") == 2


def test_editor_keeps_windows_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISUAL", r'"C:\Program Files\Code\code.exe" --wait')
    with patch("nw_cli.review.os.name", "nt"):
        assert _editor() == [r"C:\Program Files\Code\code.exe", "--wait"]


def test_redo_passes_feedback_and_keeps_the_scope_when_it_fails(
    tmp_path: Path, scope_file: Path
) -> None:
    before = scope_file.read_text()
    feedback: list[str | None] = []

    def failing(note: str | None) -> Path:
        feedback.append(note)
        scope_file.unlink()  # the scoping run deletes the old scope first
        raise StageError("claude exited 1")

    decision, _, text, _ = _review(
        tmp_path, scope_file, ["r", "only read-only tools", "s"], rescope=failing
    )
    assert feedback == ["only read-only tools"]
    assert scope_file.read_text() == before
    assert "Kept the previous scope" in text and not decision.generate
    assert not scope_file.with_suffix(".yaml.bak").exists()


def test_without_a_terminal_it_stops_and_says_why(tmp_path: Path, scope_file: Path) -> None:
    decision, _, text, asked = _review(tmp_path, scope_file, [], checks=[BROKEN], interactive=False)
    assert not decision.generate and not asked
    assert "no terminal" in text and "unknown endpoint" in text
