# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""stacklok Phase 1 for ``nw gen-stacklok --path``: run the vendored ``/ai-scoping`` skill.

Interactive (the default with a terminal): an ordinary Claude Code session. The skill asks its own
questions and stops at its USER GATEs, exactly as in stacklok's flow; command-line answers are
passed in as starting points. ``nw`` continues when the session exits.

Headless (``--headless``, or no terminal): Claude Code print mode. The command-line answers are
final, and the gates take the skill's own recommendation, recorded in ``scoping-summary.md`` for
the Phase 2 review.

Either way Claude Code is pre-approved only for file edits inside the scoping work directory and
stacklok's CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404  # fixed claude argv, no shell
from dataclasses import dataclass, field
from pathlib import Path

from nw_cli.stages import LogFn, StageError, run_logged_command

STACKLOK_BUILDER_DIR = "nw-stacklok-builder"
SKILL = "ai-scoping"
AGENTS = ("spec-analyzer", "endpoint-scoper")
FAILURE_MARKER = "NW_SCOPING_FAILED"

# The skill needs: file tools in its working directory, its sub-agents, and stacklok's CLI.
# Nothing else. The spec is untrusted input the model reads, so edits are allowed only inside
# the work directory (see allowed_tools): a prompt-injected spec must not be able to rewrite
# .claude/ or the vendored skill it links to.
ALLOWED_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Skill",
    "Agent",
    "Task",
    "Bash(uv run mcp-builder:*)",
    "Bash(uv run mcp-builder-schema:*)",
    "Bash(mkdir:*)",
    "Bash(ls:*)",
)


def allowed_tools(work_dir: Path) -> list[str]:
    """``ALLOWED_TOOLS`` plus file edits confined to ``work_dir``.

    ``Edit(...)`` rules cover every file-editing tool (Write included). ``//`` marks an absolute
    path; Claude Code matches Windows paths in POSIX form (``C:\\x`` -> ``/c/x``).
    """
    path = work_dir.resolve().as_posix()
    drive = re.match(r"([A-Za-z]):(.*)", path)
    if drive:
        path = f"/{drive.group(1).lower()}{drive.group(2)}"
    return [*ALLOWED_TOOLS, f"Edit(/{path}/**)"]


def scoping_dir(node_wire_root: Path, connector_id: str) -> Path:
    return node_wire_root / STACKLOK_BUILDER_DIR / "scoping" / connector_id


@dataclass(frozen=True)
class ScopingRequest:
    """What the ai-scoping skill would otherwise ask for."""

    spec: Path
    connector_id: str
    workflows: list[str]
    auth_hint: str | None = None
    notes: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def link_skill(node_wire_root: Path, config_dir: str = ".claude") -> None:
    """Link the vendored skill and agents into ``<root>/<config_dir>/`` (idempotent).

    Same links as ``scripts/install-stacklok-skills.sh``.
    """
    source = node_wire_root / STACKLOK_BUILDER_DIR
    target = node_wire_root / config_dir
    links = {target / "skills" / SKILL: source / "skills" / SKILL}
    links.update({target / "agents" / f"{a}.md": source / "agents" / f"{a}.md" for a in AGENTS})
    for link, real in links.items():
        if not real.exists():
            raise StageError(f"Vendored stacklok file missing: {real}")
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and link.resolve() == real.resolve():
            continue
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.exists():
            raise StageError(f"{link} exists and is not a link to the vendored stacklok file")
        link.symlink_to(real)


def scoping_prompt(request: ScopingRequest, work_dir: Path) -> str:
    """The headless ``/ai-scoping`` invocation, carrying every answer the skill would ask for."""
    if request.workflows:
        workflows = "Workflow descriptions:\n" + "\n".join(f"  - {w}" for w in request.workflows)
    else:
        workflows = (
            "Workflow descriptions: none given. Propose three distinct workflows from the spec "
            "(different personas or use cases, grounded in its operations and descriptions), "
            "use them, and list them under 'Inferred workflows' in scoping-summary.md for the "
            "Phase 2 reviewer to confirm."
        )
    auth = request.auth_hint or "none given; detect it from the spec's security schemes"
    lines = [
        f"/{SKILL} {request.spec}",
        "",
        "This run is non-interactive: `nw gen-stacklok` started it and nobody can answer",
        "questions. The user has already supplied what Step 1 asks for:",
        f"- {workflows}",
        f"- Authentication hint: {auth}",
    ]
    if request.notes:
        lines.append(f"- Scoping notes from the user: {request.notes}")
    lines += [
        f"- Working directory: {work_dir} (use it instead of scoping-output-<date>/).",
        "",
        "At every USER GATE, do not stop: take your own recommendation.",
        "- Groups: include every group relevant to the workflows.",
        "- Tools: approve the proposed tools.",
        "- Auth: accept the detected auth.",
        "- URL placeholders: use the spec's server-variable defaults.",
        "Record each such decision under an 'Auto-approved gates' heading in scoping-summary.md, so",
        "the human reviewer (Phase 2) can revisit it.",
        f"If a required value cannot be determined, write nothing further and print `{FAILURE_MARKER}:",
        "<reason>` as the last line. Finish when mcp-scope.yaml passes `uv run mcp-builder",
        "validate`.",
    ]
    return "\n".join(lines)


def interactive_prompt(request: ScopingRequest, work_dir: Path) -> str:
    """The opening message of an interactive ``/ai-scoping`` session."""
    lines = [
        f"/{SKILL} {request.spec}",
        "",
        "Started by `nw gen-stacklok`. Follow the skill as written, including every USER GATE.",
        f"- Working directory: {work_dir} (use it instead of scoping-output-<date>/).",
    ]
    if request.workflows:
        given = "\n".join(f"    - {w}" for w in request.workflows)
        lines.append(f"- Workflows the user already gave (confirm or extend them):\n{given}")
    if request.auth_hint:
        lines.append(f"- Authentication hint from the user: {request.auth_hint}")
    if request.notes:
        lines.append(f"- Scoping notes from the user: {request.notes}")
    lines += [
        "",
        "When mcp-scope.yaml passes `uv run mcp-builder validate`, tell the user to exit Claude",
        "Code (/exit). `nw gen-stacklok` then continues with the Phase 2 review and generation.",
    ]
    return "\n".join(lines)


def _claude_command() -> list[str]:
    """The argv prefix that launches Claude Code."""
    claude = os.environ.get("NW_CLAUDE_BIN") or shutil.which("claude")
    if not claude:
        raise StageError(
            "Phase 1 needs Claude Code (`claude` on PATH, or NW_CLAUDE_BIN). "
            "Or write/choose a scope and run: nw gen-stacklok --scope <mcp-scope.yaml>"
        )
    return [claude]


def _prepare_run(node_wire_root: Path, work_dir: Path) -> Path:
    link_skill(node_wire_root)
    work_dir.mkdir(parents=True, exist_ok=True)
    scope = work_dir / "mcp-scope.yaml"
    scope.unlink(missing_ok=True)
    return scope


def run_ai_scoping_interactive(
    node_wire_root: Path,
    request: ScopingRequest,
    *,
    work_dir: Path,
    model: str | None = None,
) -> Path:
    """Phase 1 in an interactive Claude Code session on this terminal; returns the scope."""
    claude = _claude_command()
    scope = _prepare_run(node_wire_root, work_dir)
    cmd = [
        *claude,
        interactive_prompt(request, work_dir),
        "--allowedTools",
        *allowed_tools(work_dir),
        "--add-dir",
        str(work_dir),
    ]
    if model:
        cmd += ["--model", model]
    # Inherit the terminal: the user talks to the skill directly.
    code = subprocess.run(cmd, cwd=node_wire_root, check=False).returncode  # nosec B603
    if not scope.is_file():
        raise StageError(
            f"The scoping session ended (claude exit {code}) without writing {scope}. "
            "Rerun the command to start it again."
        )
    return scope


def run_ai_scoping(
    node_wire_root: Path,
    request: ScopingRequest,
    *,
    work_dir: Path,
    model: str | None = None,
    log: LogFn | None = None,
) -> Path:
    """Phase 1 headless (Claude Code print mode); returns ``<work_dir>/mcp-scope.yaml``."""
    claude = _claude_command()
    scope = _prepare_run(node_wire_root, work_dir)

    cmd = [
        *claude,
        "-p",
        scoping_prompt(request, work_dir),
        # dontAsk: anything not pre-approved is refused (acceptEdits would approve edits
        # anywhere under the repo root, the process cwd).
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        *allowed_tools(work_dir),
        "--add-dir",
        str(work_dir),
    ]
    if model:
        cmd += ["--model", model]
    captured: list[str] = []

    def _log(line: str) -> None:
        captured.append(line)
        (log or print)(line)

    # Empty stdin: print mode must never read the user's terminal.
    code = run_logged_command(cmd, cwd=node_wire_root, log=_log, stdin=subprocess.DEVNULL)
    failure = next((line for line in reversed(captured) if FAILURE_MARKER in line), None)
    if failure:
        raise StageError(f"AI scoping stopped: {failure.split(FAILURE_MARKER, 1)[1].lstrip(': ')}")
    if code != 0:
        raise StageError(f"AI scoping failed (claude exit {code})")
    if not scope.is_file():
        raise StageError(f"AI scoping finished without writing {scope}")
    return scope
