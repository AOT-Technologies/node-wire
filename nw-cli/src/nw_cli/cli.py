# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Typer CLI for ``nw`` — gen-all / gen-whl / gen-mcp / docker-build / gen-stacklok."""

from __future__ import annotations

import json
import re
import tempfile
import time
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional

import typer
import yaml
from typer.core import TyperGroup

try:
    __version__ = _pkg_version("nw-cli")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"

from nw_mcp_builder.tool_listing import DEFAULT_MAX_TOOL_LISTING_KB

from nw_cli import ui
from nw_cli.menu import Option, choose
from nw_cli.names import docker_image_tag, mcp_project_dir
from nw_cli.prerequisites import confirm_build, docker_problem, is_interactive
from nw_cli.progress import GenerateProgress, Stage
from nw_cli.root import resolve_node_wire_root
from nw_cli.stages import (
    StageError,
    build_image,
    build_mode_flag,
    mcp_host_packages,
    register_all_packages,
    run_docker_build,
    run_mcp_build,
    run_wheel_build,
    stale_wheel_packages,
    wheel_packages,
)
from nw_cli.tool_mode import decide_tool_mode, describe_decision, tool_mode_from_flags
from nw_cli.ui import console, guard

_BANNER = r"""
                _                   _
 _ __   ___   __| | ___   __      _(_)_ __ ___
| '_ \ / _ \ / _` |/ _ \  \ \ /\ / / | '__/ _ \
| | | | (_) | (_| |  __/   \ V  V /| | | |  __/
|_| |_|\___/ \__,_|\___|    \_/\_/ |_|_|  \___|
"""

_HELP = """\
Turn an OpenAPI/Swagger spec into a runnable MCP server.

The pipeline runs in four stages, each also available on its own:
[bold]gen-all[/bold] (codegen → wheel → mcp → wire), then [bold]gen-whl[/bold],
[bold]gen-mcp[/bold], and [bold]docker-build[/bold].

[bold]gen-stacklok[/bold] builds a stacklok mcp-builder server (ToolHive-ready) on the
node-wire runtime from a stacklok [cyan]mcp-scope.yaml[/cyan].

Run [cyan]nw COMMAND --help[/cyan] for a command's options.
"""


class _BannerGroup(TyperGroup):
    """Print the node-wire banner + version above the group help page."""

    def format_help(self, ctx: typer.Context, formatter) -> None:  # type: ignore[override]
        console.print(_BANNER, style=ui.BLUE, highlight=False)
        console.print(f"  node-wire CLI v{__version__}", style="dim", highlight=False)
        super().format_help(ctx, formatter)


app = typer.Typer(
    name="nw",
    cls=_BannerGroup,
    help=_HELP,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    # Every command runs in ui.guard; never dump local variables (tokens, secrets) if
    # something still escapes it.
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"nw {__version__}", highlight=False)
        raise typer.Exit()


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the node-wire CLI version and exit.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Stream build tool output (always on without a terminal, e.g. in CI)",
    ),
    debug: bool = typer.Option(
        False, "--debug", help="Show the full traceback on errors (or set NW_DEBUG=1)"
    ),
) -> None:
    """Node Wire CLI entry point."""
    ui.options.verbose = verbose
    ui.options.debug = ui.options.debug or debug


_CONNECTOR_ID = re.compile(r"[a-z][a-z0-9_]*")


def _connector_id(value: Optional[str]) -> Optional[str]:
    """Checked before any work: the id becomes package, module and directory names."""
    if value is None:
        return None
    value = value.strip()
    if not _CONNECTOR_ID.fullmatch(value):
        raise typer.BadParameter(
            f"{value!r} is not a valid connector id: use a lowercase Python identifier "
            "(letters, digits, underscores; starting with a letter), e.g. pet_store"
        )
    return value


_TOOL_SEARCH_HELP = (
    "Serve the MCP host's tools through nw_search_tools + nw_call_tool (for large connectors)"
)
_FULL_TOOL_LIST_HELP = "List every tool, even over the size budget (no prompt, no warning)"
_MAX_TOOL_LISTING_HELP = (
    "Tool listing budget in KB; over it you are asked to choose full list or tool search"
)


def _tool_mode(tool_search: bool, full_tool_list: bool) -> str | None:
    try:
        return tool_mode_from_flags(tool_search, full_tool_list)
    except ValueError as exc:
        raise ui.usage_error(str(exc), flags="--tool-search / --full-tool-list") from exc


def _progress(command: str, stages: list[Stage]) -> GenerateProgress:
    return GenerateProgress(stages=stages, title=f"nw {command}", log_path=ui.new_log_file(command))


def _shown(path: Path, node_wire_root: Path) -> str:
    """``path`` relative to the repo root when inside it (shorter in the summary panel)."""
    try:
        return str(Path(path).resolve().relative_to(node_wire_root.resolve()))
    except ValueError:
        return str(path)


def _next_docker_build(connector_id: str) -> None:
    ui.next_steps(
        [
            ui.Step(
                "Build the Docker image", f"uv run nw docker-build --connector-id {connector_id}"
            )
        ],
        heading="Next step",
    )


def _gen_whl_command(packages: list[str], connector_id: str) -> str:
    flags = []
    if "packages/runtime" in packages:
        flags.append("--runtime")
    if "packages/bindings" in packages:
        flags.append("--bindings")
    if f"packages/connectors/{connector_id}" in packages:
        flags.append(f"--connector-id {connector_id}")
    return " ".join(["nw gen-whl", *flags])


def _missing_wheels(node_wire_root: Path, connector_id: str) -> list[str]:
    """The MCP host's packages with no wheel in ``dist/`` yet."""
    return [
        p
        for p in mcp_host_packages(connector_id)
        if not any((node_wire_root / p / "dist").glob("*.whl"))
    ]


def _build_connector(
    progress: GenerateProgress,
    node_wire_root: Path,
    *,
    spec: str,
    connector_id: str,
    wire: bool,
    force: bool,
    base_url: str | None = None,
) -> None:
    """nw-connector-builder codegen (never its MCP hand-off), with a short summary."""
    from nw_connector_builder.pipeline import run_build

    code = run_build(
        spec=spec,
        connector_id=connector_id,
        node_wire_root=node_wire_root,
        wire=wire,
        force=force,
        no_mcp=True,
        base_url=base_url,
    )
    report = _read_report(node_wire_root, connector_id)
    if code != 0:
        wire_error = (report.get("wire") or {}).get("error")
        if wire_error:
            raise StageError(f"Connector generated, but wiring it in failed: {wire_error}")
        raise StageError(f"Connector build returned exit code {code}")
    summary = report.get("summary") or {}
    if "generated" in summary:
        progress.log(
            f"{summary.get('generated')}/{summary.get('total_operations')} operations generated, "
            f"{summary.get('soft_dropped')} skipped (details: packages/connectors/"
            f"{connector_id}/report.json)"
        )
    if summary.get("coverage_warning"):
        progress.log("warning: fewer than half of the spec's operations became actions")


def _read_report(node_wire_root: Path, connector_id: str) -> dict:
    path = node_wire_root / "packages" / "connectors" / connector_id / "report.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return report if isinstance(report, dict) else {}


def _decide_mode(
    progress: GenerateProgress,
    node_wire_root: Path,
    connector_id: str,
    tool_mode: str | None,
    max_tool_listing_kb: float,
) -> str:
    """Tool mode for the MCP host; an over-budget question pauses the bars."""
    decision = decide_tool_mode(
        node_wire_root,
        connector_id,
        tool_mode=tool_mode,
        max_tool_listing_kb=max_tool_listing_kb,
        console=progress.console,
        notify=lambda message: progress.log(f"warning: {message}"),
        pause=progress.paused,
    )
    progress.log(describe_decision(decision))
    return decision.mode


def _build_wheels(
    progress: GenerateProgress, node_wire_root: Path, packages: list[str], *, rebuild: bool
) -> None:
    """Build the packages whose wheels are missing or older than their sources."""
    stale = list(packages) if rebuild else stale_wheel_packages(node_wire_root, packages)
    reused = [p for p in packages if p not in stale]
    if reused:
        progress.log("Wheels up to date, reused: " + ", ".join(reused))
    if stale:
        run_wheel_build(node_wire_root, packages=stale, log=progress.output)


@app.command("gen-all")
@guard
def gen_all(
    id: str = typer.Option(
        ..., "--connector-id", help="Connector id (e.g. pet_store)", callback=_connector_id
    ),
    path: str = typer.Option(..., "--path", help="OpenAPI/Swagger spec path or URL"),
    no_wheel: bool = typer.Option(False, "--no-wheel", help="Skip wheel build"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip MCP host build"),
    no_wire: bool = typer.Option(
        False, "--no-wire", help="Skip connectors.yaml / sample.env / ALL_PACKAGES"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing connector / MCP output"),
    rebuild_wheels: bool = typer.Option(
        False,
        "--rebuild-wheels",
        help="Rebuild every wheel, even those whose sources are unchanged",
    ),
    tool_search: bool = typer.Option(
        False, "--tool-search", help=_TOOL_SEARCH_HELP, rich_help_panel="Tool listing"
    ),
    full_tool_list: bool = typer.Option(
        False, "--full-tool-list", help=_FULL_TOOL_LIST_HELP, rich_help_panel="Tool listing"
    ),
    max_tool_listing_kb: float = typer.Option(
        DEFAULT_MAX_TOOL_LISTING_KB,
        "--max-tool-listing-kb",
        help=_MAX_TOOL_LISTING_HELP,
        rich_help_panel="Tool listing",
    ),
) -> None:
    """One-shot: connector codegen → wheel → MCP host → wire."""
    tool_mode = _tool_mode(tool_search, full_tool_list)
    node_wire_root = resolve_node_wire_root()
    packages = mcp_host_packages(id)

    if no_wheel and not no_mcp:
        # Fail before codegen, not after it: the MCP host bundles these wheels.
        missing = _missing_wheels(node_wire_root, id)
        if missing:
            raise StageError(
                f"--no-wheel, but the MCP host needs wheels that are missing: {', '.join(missing)}. "
                f"Drop --no-wheel, or build them first: {_gen_whl_command(missing, id)}"
            )

    rerun = f"nw gen-all --connector-id {id} --path {path}"
    progress = _progress(
        "gen-all",
        [
            Stage("connector", "Connector codegen", hint="Check the --path spec, then rerun."),
            Stage(
                "wheel",
                "Wheel build",
                hint=f"Needs Docker running. Retry just this step: {_gen_whl_command(packages, id)}",
            ),
            Stage(
                "mcp",
                "MCP host build",
                hint=f"Retry just this step: nw gen-mcp --connector-id {id}"
                + (" --force-output" if force else ""),
            ),
            Stage(
                "wire",
                "Register in ALL_PACKAGES",
                hint="Check the ALL_PACKAGES block in scripts/build-packages.sh, then rerun: "
                + rerun,
            ),
        ],
    )
    for key, skip in (("wheel", no_wheel), ("mcp", no_mcp), ("wire", no_wire)):
        if skip:
            progress.mark_skipped(key)

    with progress:
        progress.run_stage(
            "connector",
            lambda: _build_connector(
                progress,
                node_wire_root,
                spec=path,
                connector_id=id,
                wire=not no_wire,
                force=force,
            ),
        )
        progress.result("Connector", f"packages/connectors/{id}")

        chosen_mode = None
        if not no_mcp:
            # Needs the generated source, and comes before the slow wheel
            # builds so an over-budget question is not kept waiting.
            chosen_mode = _decide_mode(progress, node_wire_root, id, tool_mode, max_tool_listing_kb)

        progress.run_stage(
            "wheel",
            lambda: _build_wheels(progress, node_wire_root, packages, rebuild=rebuild_wheels),
        )

        if not no_mcp:
            project = progress.run_stage(
                "mcp",
                lambda: run_mcp_build(
                    node_wire_root, id, force_output=force, tool_mode=chosen_mode
                ),
            )
            progress.result("MCP host", _shown(project, node_wire_root))

        progress.run_stage("wire", lambda: register_all_packages(node_wire_root, id))
    if not no_mcp:
        _next_docker_build(id)


@app.command("gen-whl")
@guard
def gen_whl(
    id: Optional[str] = typer.Option(
        None,
        "--connector-id",
        help="Connector id (required unless --runtime/--bindings)",
        callback=_connector_id,
    ),
    host: bool = typer.Option(False, "--host", help="Host-only wheel build"),
    all_: bool = typer.Option(False, "--all", help="Full cibuildwheel matrix"),
    runtime: bool = typer.Option(False, "--runtime", help="Build packages/runtime"),
    bindings: bool = typer.Option(
        False, "--bindings", help="Build packages/bindings (MCP host surface)"
    ),
) -> None:
    """Build binary wheels via scripts/build-packages.sh (Linux-only by default)."""
    if host and all_:
        raise ui.usage_error("they are mutually exclusive", flags="--host / --all")
    packages = wheel_packages(connector_id=id, runtime=runtime, bindings=bindings)
    if not packages:
        raise ui.usage_error(
            "required unless --runtime and/or --bindings is set", flags="--connector-id"
        )
    node_wire_root = resolve_node_wire_root()
    mode = build_mode_flag(host=host, all_=all_).lstrip("-")

    progress = _progress(
        "gen-whl",
        [
            Stage(
                "wheel",
                f"Wheels ({mode})",
                hint=""
                if host
                else "Linux wheels need Docker running; --host builds for this machine only.",
            )
        ],
    )
    with progress:
        progress.run_stage(
            "wheel",
            lambda: run_wheel_build(
                node_wire_root, packages=packages, host=host, all_=all_, log=progress.output
            ),
        )
        progress.result("Built", ", ".join(packages))


def _mcp_host_stages(
    connector_id: str, *, force_output: bool, wheels: bool, host: bool = True
) -> list[Stage]:
    """Only the prerequisite stages this run needs: nothing already built is listed."""
    stages = []
    if wheels:
        stages.append(
            Stage(
                "wheel",
                "Wheel build",
                hint="Needs Docker running. Build them yourself with: "
                + _gen_whl_command(mcp_host_packages(connector_id), connector_id),
            )
        )
    if host:
        stages.append(
            Stage(
                "mcp",
                "MCP host build",
                hint=f"Retry: nw gen-mcp --connector-id {connector_id}"
                + (" --force-output" if force_output else ""),
            )
        )
    return stages


@app.command("gen-mcp")
@guard
def gen_mcp(
    id: str = typer.Option(..., "--connector-id", help="Connector id", callback=_connector_id),
    force_output: bool = typer.Option(
        False, "--force-output", help="Replace existing out/<server>-mcp/"
    ),
    tool_search: bool = typer.Option(
        False, "--tool-search", help=_TOOL_SEARCH_HELP, rich_help_panel="Tool listing"
    ),
    full_tool_list: bool = typer.Option(
        False, "--full-tool-list", help=_FULL_TOOL_LIST_HELP, rich_help_panel="Tool listing"
    ),
    max_tool_listing_kb: float = typer.Option(
        DEFAULT_MAX_TOOL_LISTING_KB,
        "--max-tool-listing-kb",
        help=_MAX_TOOL_LISTING_HELP,
        rich_help_panel="Tool listing",
    ),
) -> None:
    """Build MCP host from an existing connector (requires wheels)."""
    tool_mode = _tool_mode(tool_search, full_tool_list)
    node_wire_root = resolve_node_wire_root()

    missing = _missing_wheels(node_wire_root, id)
    if missing:
        confirm_build(
            f"No wheels yet for {', '.join(missing)} — build them now?",
            fix_command=_gen_whl_command(missing, id),
        )

    progress = _progress(
        "gen-mcp", _mcp_host_stages(id, force_output=force_output, wheels=bool(missing))
    )
    with progress:
        mode = _decide_mode(progress, node_wire_root, id, tool_mode, max_tool_listing_kb)
        if missing:
            progress.run_stage(
                "wheel",
                lambda: run_wheel_build(node_wire_root, packages=missing, log=progress.output),
            )
        project = progress.run_stage(
            "mcp",
            lambda: run_mcp_build(node_wire_root, id, force_output=force_output, tool_mode=mode),
        )
        progress.result("MCP host", _shown(project, node_wire_root))
    _next_docker_build(id)


@app.command("docker-build")
@guard
def docker_build(
    id: Optional[str] = typer.Option(
        None,
        "--connector-id",
        help="Build the project generated for this connector: its gen-all / gen-mcp host or "
        "gen-stacklok server (asks when there are several)",
        callback=_connector_id,
    ),
    project_dir: Optional[Path] = typer.Option(
        None,
        "--project",
        help="Build a generated project by path instead: one written outside the default "
        "output folders (gen-stacklok --output-dir); the image is named after the folder",
    ),
    tag: str = typer.Option("latest", "--tag", help="Docker image tag"),
) -> None:
    """Build a Docker image from a generated MCP project."""
    if (id is None) == (project_dir is None):
        raise ui.usage_error("pass exactly one of them", flags="--connector-id / --project")
    if project_dir is not None:
        _build_project_image(project_dir, tag)
        return
    node_wire_root = resolve_node_wire_root()
    generated = _image_projects(node_wire_root, id)
    if generated:
        chosen = _pick_project(generated, id)
        _build_project_image(chosen.path, tag, image=chosen.image(tag))
        return
    project = mcp_project_dir(node_wire_root, id)

    need_host = not project.is_dir()
    missing = _missing_wheels(node_wire_root, id) if need_host else []
    if need_host:
        also = f" (and the missing wheels: {', '.join(missing)})" if missing else ""
        confirm_build(
            f"MCP project not found at {project} — generate it now{also}?",
            fix_command=f"nw gen-mcp --connector-id {id}",
        )

    progress = _progress(
        "docker-build",
        [
            *_mcp_host_stages(id, force_output=False, wheels=bool(missing), host=need_host),
            Stage(
                "docker",
                "Docker image",
                hint=f"Check the Dockerfile in {project}; rerun with --verbose to see every step.",
            ),
        ],
    )
    with progress:
        if need_host:
            mode = _decide_mode(progress, node_wire_root, id, None, DEFAULT_MAX_TOOL_LISTING_KB)
            if missing:
                progress.run_stage(
                    "wheel",
                    lambda: run_wheel_build(node_wire_root, packages=missing, log=progress.output),
                )
            progress.run_stage(
                "mcp",
                lambda: run_mcp_build(node_wire_root, id, force_output=True, tool_mode=mode),
            )
        image = progress.run_stage(
            "docker",
            lambda: run_docker_build(node_wire_root, id, tag=tag, log=progress.output),
        )
        progress.result("Image", image)


@dataclass(frozen=True)
class _ImageProject:
    kind: str
    path: Path
    host: bool  # a gen-all / gen-mcp host (its image keeps the <server>-nw-mcp name)
    connector_id: str

    def image(self, tag: str) -> str:
        return docker_image_tag(self.connector_id, tag) if self.host else f"{self.path.name}:{tag}"


def _stacklok_connectors(project: Path) -> set[str]:
    try:
        doc = yaml.safe_load((project / "config" / "connectors.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    connectors = (doc or {}).get("connectors") if isinstance(doc, dict) else None
    return set(connectors) if isinstance(connectors, dict) else set()


def _image_projects(node_wire_root: Path, connector_id: str) -> list[_ImageProject]:
    """Generated projects with a Dockerfile for ``connector_id``, newest first."""
    from nw_cli.stacklok import default_output_dir

    found = []
    host = mcp_project_dir(node_wire_root, connector_id)
    if (host / "Dockerfile").is_file():
        found.append(_ImageProject("MCP host (gen-mcp)", host, True, connector_id))
    out = default_output_dir(node_wire_root)
    for project in sorted(out.glob("*-mcp")) if out.is_dir() else []:
        if (project / "Dockerfile").is_file() and connector_id in _stacklok_connectors(project):
            found.append(_ImageProject("stacklok server", project, False, connector_id))
    return sorted(found, key=lambda p: p.path.stat().st_mtime, reverse=True)


def _pick_project(projects: list[_ImageProject], connector_id: str) -> _ImageProject:
    """The only project, the user's pick from a menu, or (no terminal) the newest."""
    if len(projects) == 1:
        return projects[0]

    def age(project: _ImageProject) -> str:
        stamp = time.localtime(project.path.stat().st_mtime)
        return time.strftime("generated %Y-%m-%d %H:%M", stamp)

    if not is_interactive():
        newest = projects[0]
        ui.warning(
            f"{len(projects)} generated projects for {connector_id}; building the newest, "
            f"{ui.display_path(newest.path)} (pass --project to choose another)"
        )
        return newest
    options = [
        Option(str(i + 1), f"{p.kind}: {ui.display_path(p.path)}", age(p))
        for i, p in enumerate(projects)
    ]
    key = choose(console, f"Which {connector_id} project?", options)
    return projects[int(key) - 1]


def _build_project_image(project: Path, tag: str, *, image: str | None = None) -> None:
    if not (project / "Dockerfile").is_file():
        raise ui.usage_error(
            f"{project} is not a generated project (no Dockerfile)", flags="--project"
        )
    progress = _progress(
        "docker-build",
        [
            Stage(
                "docker",
                "Docker image",
                hint=f"Check the Dockerfile in {ui.display_path(project)}; "
                "rerun with --verbose to see every step.",
            )
        ],
    )
    with progress:
        image = progress.run_stage(
            "docker",
            lambda: build_image(
                project, image or f"{project.resolve().name}:{tag}", log=progress.output
            ),
        )
        progress.result("Image", image)


@app.command("gen-stacklok")
@guard
def gen_stacklok(
    path: Optional[str] = typer.Option(
        None,
        "--path",
        help="OpenAPI/Swagger spec path or URL: run stacklok Phases 1–3 (AI scoping → review → generate)",
    ),
    scope: Optional[Path] = typer.Option(
        None,
        "--scope",
        help="An existing stacklok mcp-scope.yaml: skip to Phase 3 (generate)",
    ),
    id: Optional[str] = typer.Option(
        None,
        "--connector-id",
        help="node-wire connector id (required with --path; default for --scope: runtime.connector_id)",
        callback=_connector_id,
    ),
    workflow: Optional[list[str]] = typer.Option(
        None,
        "--workflow",
        help="Phase 1: what users do with the API (repeatable; three recommended). "
        "Without it the AI proposes workflows for you to confirm in the review",
        rich_help_panel="Phase 1: AI scoping",
    ),
    auth_hint: Optional[str] = typer.Option(
        None,
        "--auth-hint",
        help="Phase 1: auth guidance for the scoping AI (default: detect from the spec)",
        rich_help_panel="Phase 1: AI scoping",
    ),
    scoping_notes: Optional[str] = typer.Option(
        None,
        "--scoping-notes",
        help="Phase 1: extra instructions, e.g. 'read-only tools, at most 10'",
        rich_help_panel="Phase 1: AI scoping",
    ),
    headless: bool = typer.Option(
        False,
        "--headless",
        help="Phase 1: run /ai-scoping unattended (claude -p); its approval gates take the AI's "
        "recommendation. Default with a terminal: an interactive session with the real gates",
        rich_help_panel="Phase 1: AI scoping",
    ),
    scoping_model: Optional[str] = typer.Option(
        None,
        "--scoping-model",
        help="Phase 1: Claude model for the scoping run (default: Claude Code's)",
        rich_help_panel="Phase 1: AI scoping",
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Where to write <server>-mcp/ (default nw-stacklok-builder/out)"
    ),
    rescope: bool = typer.Option(
        False,
        "--rescope",
        help="Phase 1: redo AI scoping even if nw-stacklok-builder/scoping/<id>/ has a scope",
        rich_help_panel="Phase 1: AI scoping",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing output project without asking (the saved scope is kept)",
    ),
    no_wheel: bool = typer.Option(
        False, "--no-wheel", help="Skip the wheel build and bundle the wheels already in dist/"
    ),
    no_lock: bool = typer.Option(
        False, "--no-lock", help="Do not run `uv lock` in the generated project"
    ),
) -> None:
    """stacklok mcp-builder on node-wire, Phase 1 → 3 in one command.

    With --path: prepare the spec, run stacklok's /ai-scoping skill in Claude Code (interactive,
    with its approval gates; --headless for unattended), pause for your review (continue, or stop
    to edit), then generate.
    With --scope: generate from an existing, reviewed scope.
    """
    from nw_cli.scoping import (
        ScopingRequest,
        run_ai_scoping,
        run_ai_scoping_interactive,
        scoping_dir,
    )
    from nw_cli.review import review_scope
    from nw_cli.stacklok import default_output_dir, prepare_spec, prepared_spec_path

    if (scope is None) == (path is None):
        raise ui.usage_error("pass exactly one of them", flags="--path / --scope")
    if path is not None and not id:
        raise ui.usage_error("required with --path", flags="--connector-id")
    if scope is not None and not scope.is_file():
        raise ui.usage_error(f"{scope} does not exist", flags="--scope")
    node_wire_root = resolve_node_wire_root()

    resume = (
        f"uv run nw gen-stacklok --path {path} --connector-id {id}"
        if path is not None
        else f"uv run nw gen-stacklok --scope {scope}"
    )
    stages = [
        Stage(
            "connector",
            "Connector codegen",
            hint="Check the scope's spec.source / spec.base_url, then rerun: " + resume,
        ),
        Stage(
            "wheel",
            "Wheels for the MCP image",
            hint="Needs Docker running and cibuildwheel (uv sync --all-extras --dev). "
            "Rerun: " + resume,
        ),
        Stage("stacklok", "stacklok MCP server", hint="Rerun: " + resume),
    ]
    if path is not None:
        stages = [
            Stage("spec", "Spec (strict OpenAPI 3.0)", hint="Check the --path spec or URL."),
            Stage("scoping", "Phase 1: AI scoping", hint="Rerun: " + resume),
            Stage("review", "Phase 2: review"),
            *stages,
        ]
        if not no_wheel and docker_problem():
            # Warn before the (long, human) scoping phase, not after it.
            ui.warning(
                f"Docker is not usable ({docker_problem()}); the wheel stage will need it. "
                "The scope is saved, so you can rerun once Docker is up."
            )
    progress = _progress("gen-stacklok", stages)
    if no_wheel:
        progress.mark_skipped("wheel")

    scope_file = scope
    if path is not None:
        assert id is not None
        work_dir = scoping_dir(node_wire_root, id)
        existing = work_dir / "mcp-scope.yaml"
        reuse = existing.is_file() and not rescope
        if reuse:
            progress.mark_skipped("scoping")
        with progress:
            prepared = progress.run_stage(
                "spec",
                lambda: prepare_spec(path, prepared_spec_path(node_wire_root, id)),
            )
            reused_at = existing.stat().st_mtime if reuse else None
            request = ScopingRequest(
                spec=prepared,
                connector_id=id,
                workflows=list(workflow or []),
                auth_hint=auth_hint,
                notes=scoping_notes,
            )
            interactive = not headless and is_interactive()

            def _scoping(feedback: str | None = None) -> Path:
                if feedback:
                    notes = f"{scoping_notes}\n" if scoping_notes else ""
                    run_request = replace(
                        request,
                        notes=f"{notes}Reviewer feedback on the previous scope: {feedback}",
                    )
                else:
                    run_request = request
                if interactive:
                    with progress.paused():
                        console.print(
                            "\n[bold]Phase 1: AI scoping[/bold]: opening Claude Code with "
                            "/ai-scoping. Answer its questions; exit (/exit) when it's done.\n",
                            highlight=False,
                        )
                        return run_ai_scoping_interactive(
                            node_wire_root, run_request, work_dir=work_dir, model=scoping_model
                        )
                return run_ai_scoping(
                    node_wire_root,
                    run_request,
                    work_dir=work_dir,
                    model=scoping_model,
                    log=progress.log,
                )

            produced = progress.run_stage("scoping", _scoping)
            decision, scope_file = progress.run_stage(
                "review",
                lambda: review_scope(
                    progress,
                    node_wire_root=node_wire_root,
                    scope_file=produced or existing,
                    connector_id=id,
                    summary=work_dir / "scoping-summary.md",
                    output_dir=output_dir or default_output_dir(node_wire_root),
                    force=force,
                    reused_at=reused_at,
                    rescope=_scoping,
                ),
            )
            if decision.generate:
                project = _stacklok_phase3(
                    progress,
                    node_wire_root,
                    scope_file,
                    id,
                    output_dir,
                    force or decision.replace_output,
                    no_lock,
                    reviewed=True,
                )
            else:
                progress.mark_stopped("review")
        if not decision.generate:
            ui.next_steps(
                [
                    ui.Step(
                        "When the scope is ready, resume from Phase 3",
                        f"uv run nw gen-stacklok --scope {ui.display_path(scope_file)}"
                        f" --connector-id {id}",
                    )
                ],
                heading="Stopped for review",
            )
            return
    else:
        assert scope_file is not None
        with progress:
            project = _stacklok_phase3(
                progress, node_wire_root, scope_file, id, output_dir, force, no_lock
            )
    ui.next_steps(
        _run_steps(
            project,
            _scoped_connector(project, id),
            default_output=output_dir is None,
        ),
        note=f"Kubernetes: {ui.display_path(project / 'deploy' / 'README.md')}. "
        f"Rerunning? docker rm -f {project.name} first.",
    )


_HOST_PORT = 8200  # published host port; the server listens on 8100 inside the image


def _scoped_connector(project: Path, connector_id: str | None) -> str:
    """The connector a generated stacklok project serves (``--scope`` may omit the id)."""
    if connector_id:
        return connector_id
    return next(iter(sorted(_stacklok_connectors(project))), "<connector_id>")


def _token_placeholder(project: Path, connector_id: str) -> str:
    """``<SLACK_WEB_ACCESS_TOKEN>``: the connector's own secret name, else a generic one."""
    try:
        doc = yaml.safe_load((project / "config" / "connectors.yaml").read_text(encoding="utf-8"))
        key = doc["connectors"][connector_id]["auth"]["secret_key"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return "<upstream-api-token>"
    return f"<{key}>"


def _run_steps(project: Path, connector_id: str, *, default_output: bool = True) -> list[ui.Step]:
    """Build → start → check → ToolHive, for a generated stacklok project (its real names)."""
    image = project.name  # <server>-mcp
    target = (
        f"--connector-id {connector_id}"
        if default_output
        else f"--project {ui.display_path(project)}"  # docker-build only looks in the default
    )
    url = f"http://127.0.0.1:{_HOST_PORT}/mcp"
    return [
        ui.Step(f"Build the {image} image", f"uv run nw docker-build {target}"),
        ui.Step(
            f"Start it as a container, serving {url}",
            # No --rm: a container that fails to start keeps its logs for `docker logs`.
            f"docker run -d --name {image} -p {_HOST_PORT}:8100 "
            f"-e NW_MULTITENANCY_ENABLED=false {image}",
        ),
        ui.Step('Check it started (look for "Starting MCP server")', f"docker logs {image}"),
        ui.Step(
            "Register it with ToolHive (proxies the running container, adds your token)",
            f"thv run {url} --name {image} --transport streamable-http "
            f"--remote-auth-bearer-token {_token_placeholder(project, connector_id)}",
        ),
    ]


def _confirm_replace(progress: GenerateProgress, project: Path) -> bool:
    """Before any Phase 3 work: may an existing output project be replaced?"""
    if not project.exists():
        return False
    if not is_interactive():
        raise StageError(f"Output project already exists: {project} (pass --force to replace it)")
    from rich.prompt import Confirm

    with progress.paused():
        if Confirm.ask(f"Replace the existing output project {project}?", default=False):
            return True
    raise StageError(f"Kept {project}; pass --output-dir to generate somewhere else")


def _stacklok_phase3(
    progress: GenerateProgress,
    node_wire_root: Path,
    scope_file: Path,
    connector_id: str | None,
    output_dir: Path | None,
    force: bool,
    no_lock: bool,
    *,
    reviewed: bool = False,
) -> Path:
    """Phase 3: connector → musllinux wheels → stacklok generator on node-wire.

    ``reviewed``: Phase 2 already validated the scope and settled replacing the output.
    """
    from nw_cli.stacklok import (
        default_output_dir,
        read_scope,
        run_stacklok_generate,
        run_stacklok_wheel_build,
        validate_stacklok_scope,
    )

    scoped = read_scope(scope_file, connector_id)
    out = output_dir or default_output_dir(node_wire_root)
    if not reviewed:
        # Seconds, before the minutes of codegen and wheel builds an invalid scope would waste.
        with tempfile.TemporaryDirectory(prefix="nw-validate-") as work:
            check, _, _ = validate_stacklok_scope(scoped, Path(work))
        if not check.ok:
            raise StageError(
                f"Scope {scope_file} fails stacklok validation:\n"
                + "\n".join(f"  - {e}" for e in check.errors)
            )
    replace_output = force or _confirm_replace(progress, out / f"{scoped.server_name}-mcp")

    # Always regenerated: the scope's endpoints must map onto a connector built from this
    # very spec. nw-connector-builder still refuses to overwrite a hand-written connector.
    progress.run_stage(
        "connector",
        lambda: _build_connector(
            progress,
            node_wire_root,
            spec=scoped.connector_spec_source,
            connector_id=scoped.connector_id,
            wire=True,
            force=True,
            base_url=scoped.base_url,
        ),
    )
    progress.run_stage(
        "wheel",
        lambda: run_stacklok_wheel_build(
            node_wire_root, scoped.connector_id, log=progress.log, output=progress.output
        ),
    )
    with tempfile.TemporaryDirectory(prefix="nw-stacklok-") as work:
        project = progress.run_stage(
            "stacklok",
            lambda: run_stacklok_generate(
                node_wire_root,
                scoped,
                output_dir=out,
                work_dir=Path(work),
                force_output=replace_output,
                lock=not no_lock,
                log=progress.log,
            ),
        )
    progress.result("MCP server", _shown(project, node_wire_root))
    return project


if __name__ == "__main__":
    app()
