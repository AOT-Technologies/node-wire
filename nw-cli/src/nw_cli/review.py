# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Phase 2 of ``nw gen-stacklok``: the human review of an AI-written ``mcp-scope.yaml``.

The reviewer sees a short summary (tools, auth, stacklok's validation, how many points the AI
flagged, what will be written), can open the details, and picks what happens next from an
arrow-key menu: generate, edit and review again, redo the AI scoping with feedback, or stop. Nothing slow runs until they choose to
generate, so an invalid scope is caught here, not after the wheel builds.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess  # nosec B404  # the user's own $VISUAL / $EDITOR, arg list, no shell
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from nw_cli.menu import Option, choose, key_session
from nw_cli.prerequisites import is_interactive
from nw_cli.progress import GenerateProgress
from nw_cli.stacklok import ScopeCheck, StacklokScope, read_scope, validate_stacklok_scope
from nw_cli.stages import StageError
from nw_cli.ui import AMBER, BLUE, PINK

# Sections of the skill's scoping-summary.md written for the reviewer.
ATTENTION_HEADINGS = ("Flagged for Phase 2 Review", "Auto-approved gates", "Inferred workflows")

Rescope = Callable[[str | None], Path]  # reviewer feedback -> the new scope file


@dataclass(frozen=True)
class ReviewDecision:
    generate: bool
    replace_output: bool = False


def _shown(path: Path | str, root: Path) -> str:
    if str(path).startswith(("http://", "https://")):
        return str(path)
    path = Path(path)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def attention_items(summary: Path) -> dict[str, list[str]]:
    """The bullets under the reviewer-facing headings of ``scoping-summary.md``."""
    if not summary.is_file():
        return {}
    items: dict[str, list[str]] = {}
    current: str | None = None
    for raw in summary.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            current = next((h for h in ATTENTION_HEADINGS if heading.lower() == h.lower()), None)
            continue
        if current and line.startswith(("- ", "* ")):
            text = line[2:].strip()
            for box in ("[ ] ", "[x] ", "[X] "):
                text = text.removeprefix(box)
            items.setdefault(current, []).append(text)
    return items


def _tools(document: dict) -> list[tuple[str, str, str]]:
    rows = []
    for group in document.get("groups") or []:
        for tool in (group or {}).get("tools") or []:
            rows.append(
                (
                    str(tool.get("tool_name", "?")),
                    str(tool.get("endpoint", "")),
                    str(group.get("name", "")),
                )
            )
    return rows


MAX_TOOL_NAMES = 10


def scope_summary(
    scoped: StacklokScope,
    check: ScopeCheck,
    *,
    node_wire_root: Path,
    summary: Path,
    output_project: Path,
    replaces_output: bool,
    reused_at: float | None,
) -> RenderableType:
    """The few facts a reviewer needs to decide; the rest is behind "Show details"."""
    tools = _tools(scoped.document)
    auth = (scoped.document.get("auth") or {}).get("type") or "none"
    flagged = sum(len(items) for items in attention_items(summary).values())
    lines: list[RenderableType] = []
    if reused_at is not None:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(reused_at))
        lines.append(Text(f"Saved scope from {stamp} (reused)", style="dim"))
    lines.append(
        Text.assemble((scoped.server_name, "bold"), f"  ·  {len(tools)} tools  ·  auth: {auth}")
    )
    names = [name for name, _, _ in tools]
    more = f", … {len(names) - MAX_TOOL_NAMES} more" if len(names) > MAX_TOOL_NAMES else ""
    lines.append(Text(", ".join(names[:MAX_TOOL_NAMES]) + more, style="dim"))
    if check.ok:
        note = f" ({len(check.warnings)} warnings in the log)" if check.warnings else ""
        lines.append(Text(f"✓ Passes stacklok validation{note}", style=BLUE))
    else:
        lines.append(Text(f"✗ {len(check.errors)} validation error(s):", style=PINK))
        lines += [Text(f"  - {e}", style=PINK) for e in check.errors]
    if flagged:
        lines.append(
            Text(f"! {flagged} point(s) flagged for your review (Show details)", style=AMBER)
        )
    target = _shown(output_project, node_wire_root)
    lines.append(
        Text(f"Writes {target}" + ("  (replaces the existing project)" if replaces_output else ""))
    )
    return Group(*lines)


def scope_details(scoped: StacklokScope, *, node_wire_root: Path, summary: Path) -> RenderableType:
    """Tools with their endpoints, the points flagged for review, and where the files are."""
    tools = _tools(scoped.document)
    table = Table(show_edge=False, pad_edge=False, header_style="bold", box=None)
    table.add_column("Tool", style=BLUE)
    table.add_column("Endpoint")
    table.add_column("Group", style="dim")
    for name, endpoint, group in tools:
        table.add_row(name, endpoint, group)
    parts: list[RenderableType] = [table]
    for heading, items in attention_items(summary).items():
        parts.append(Text(f"\n{heading}", style=f"bold {AMBER}"))
        # Markdown: hanging indents for wrapped bullets, `code` styled.
        parts.append(Markdown("\n".join(f"- {item}" for item in items)))
    files = Table.grid(padding=(0, 2))
    files.add_column(style="bold")
    files.add_column(overflow="fold")
    files.add_row("Scope", _shown(scoped.path, node_wire_root))
    if summary.is_file():
        files.add_row("Reasoning", _shown(summary, node_wire_root))
    parts += [Text(""), files]
    return Group(*parts)


def _editor() -> list[str] | None:
    command = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    return shlex.split(command) if command else None


def _edit(console: Console, scope_file: Path) -> None:
    editor = _editor()
    if editor:
        try:
            subprocess.run([*editor, str(scope_file)], check=False)  # nosec B603
            return
        except OSError as exc:
            console.print(f"[{PINK}]Cannot start {editor[0]}: {exc}[/]", highlight=False)
    console.print(
        f"Edit [bold]{scope_file}[/bold] in your editor and save it, then press Enter to review "
        "it again.",
        highlight=False,
        soft_wrap=True,
    )
    console.input()


def _redo(console: Console, rescope: Rescope, scope_file: Path) -> Path | None:
    """Rerun the AI scoping with the reviewer's feedback; the old scope survives a failure."""
    feedback = Prompt.ask(
        "What should the AI change? [dim](Enter to just rerun it)[/dim]",
        default="",
        show_default=False,
        console=console,
    ).strip()
    backup = scope_file.with_suffix(".yaml.bak")
    shutil.copy2(scope_file, backup)
    try:
        produced = rescope(feedback or None)
    except StageError as exc:
        shutil.copy2(backup, scope_file)
        console.print(
            f"[{PINK}]AI scoping failed:[/] {exc}\nKept the previous scope.", highlight=False
        )
        return None
    finally:
        if not scope_file.exists():
            shutil.copy2(backup, scope_file)
        backup.unlink(missing_ok=True)
    return produced


def _menu(
    check: ScopeCheck, *, replaces: str | None, editor: list[str] | None, details: bool
) -> list[Option]:
    options = []
    if check.ok:
        options.append(
            Option("g", "Generate the MCP server", f"replaces {replaces}" if replaces else "")
        )
    if details:
        options.append(Option("d", "Show details", "tools, endpoints, flagged points"))
    options += [
        Option("e", "Edit the scope", f"opens {editor[0]}" if editor else "then review again"),
        Option("r", "Redo the AI scoping", "tell the AI what to change"),
        Option("s", "Stop here", "resume later with --scope"),
    ]
    return options


def review_scope(
    progress: GenerateProgress,
    *,
    node_wire_root: Path,
    scope_file: Path,
    connector_id: str | None,
    summary: Path,
    output_dir: Path,
    force: bool,
    reused_at: float | None,
    rescope: Rescope,
) -> tuple[ReviewDecision, Path]:
    """Show the scope and loop until the reviewer generates or stops; returns the final scope."""
    console = progress.console
    while True:
        try:
            scoped = read_scope(scope_file, connector_id)
        except StageError as exc:
            scoped, check = None, ScopeCheck(errors=[str(exc)], warnings=[])
        else:
            with tempfile.TemporaryDirectory(prefix="nw-review-") as work:
                check, _, _ = validate_stacklok_scope(scoped, Path(work))
        for warning in check.warnings:
            progress.log_only(f"scope warning: {warning}")

        project = output_dir / f"{scoped.server_name}-mcp" if scoped else None
        replaces = project is not None and project.exists() and not force

        if not is_interactive():
            verdict = (
                "passes validation" if check.ok else f"{len(check.errors)} validation error(s)"
            )
            progress.log(
                f"Phase 2 needs a human review and there is no terminal; stopping ({verdict})."
            )
            for error in check.errors:
                progress.log(f"  - {error}")
            return ReviewDecision(generate=False), scope_file

        editor = _editor()
        options = _menu(
            check,
            replaces=_shown(project, node_wire_root) if replaces else None,
            editor=editor,
            details=scoped is not None,
        )
        with progress.paused():
            if scoped is not None:
                body: RenderableType = scope_summary(
                    scoped,
                    check,
                    node_wire_root=node_wire_root,
                    summary=summary,
                    output_project=project,  # type: ignore[arg-type]
                    replaces_output=replaces,
                    reused_at=reused_at,
                )
            else:
                body = Text(f"✗ {check.errors[0]}", style=PINK)
            console.print(
                Panel(
                    body, title="Phase 2: review the scope", title_align="left", border_style=AMBER
                )
            )
            with key_session(console) as keys:
                while True:
                    choice = choose(console, "Next step", options, key_reader=keys)
                    if choice != "d":
                        break
                    assert scoped is not None
                    console.print(
                        Panel(
                            scope_details(scoped, node_wire_root=node_wire_root, summary=summary),
                            title="Scope details",
                            title_align="left",
                            border_style="grey50",
                        )
                    )
            if choice == "g":
                return ReviewDecision(generate=True, replace_output=replaces), scope_file
            if choice == "s":
                return ReviewDecision(generate=False), scope_file
            if choice == "e":
                _edit(console, scope_file)
            elif choice == "r":
                produced = _redo(console, rescope, scope_file)
                if produced is not None:
                    scope_file, reused_at = produced, None
