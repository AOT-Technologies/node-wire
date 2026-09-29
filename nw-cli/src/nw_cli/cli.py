# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Typer CLI for ``nw`` — gen-all / gen-whl / gen-mcp / docker-build."""

from __future__ import annotations

import tempfile
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from typer.core import TyperGroup

try:
    __version__ = _pkg_version("nw-cli")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"

from nw_cli.names import mcp_project_dir
from nw_cli.prerequisites import ensure, is_interactive
from nw_cli.progress import GenerateProgress, Stage
from nw_cli.root import RootError, resolve_node_wire_root
from nw_cli.tool_mode import decide_tool_mode, describe_decision, tool_mode_from_flags
from nw_connector_builder.pipeline import BuildError, UsageError
from nw_mcp_builder.tool_listing import DEFAULT_MAX_TOOL_LISTING_KB
from nw_cli.stages import (
    StageError,
    bindings_wheel_present,
    connector_wheel_present,
    register_all_packages,
    run_docker_build,
    run_mcp_build,
    run_wheel_build,
    runtime_wheel_present,
    wheels_present,
)

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

console = Console()
err_console = Console(stderr=True)


class _BannerGroup(TyperGroup):
    """Print the node-wire banner + version above the group help page."""

    def format_help(self, ctx: typer.Context, formatter) -> None:  # type: ignore[override]
        console.print(_BANNER, style="#37c4f0", highlight=False)
        console.print(f"  node-wire CLI v{__version__}", style="dim", highlight=False)
        super().format_help(ctx, formatter)


app = typer.Typer(
    name="nw",
    cls=_BannerGroup,
    help=_HELP,
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
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
) -> None:
    """Node Wire CLI entry point."""


_TOOL_SEARCH_HELP = (
    "Serve the MCP host's tools through nw_search_tools + nw_call_tool (for large connectors)"
)
_FULL_TOOL_LIST_HELP = "List every tool, even over the size budget (no prompt, no warning)"
_MAX_TOOL_LISTING_HELP = (
    "Tool listing budget in KB; over it you are asked to choose full list or tool search"
)


def _tool_mode_or_exit(tool_search: bool, full_tool_list: bool) -> str | None:
    try:
        return tool_mode_from_flags(tool_search, full_tool_list)
    except ValueError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(2) from exc


def _root() -> Path:
    try:
        return resolve_node_wire_root()
    except RootError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("gen-all")
def gen_all(
    id: str = typer.Option(..., "--connector-id", help="Connector id (e.g. pet_store)"),
    path: str = typer.Option(..., "--path", help="OpenAPI/Swagger spec path or URL"),
    no_wheel: bool = typer.Option(False, "--no-wheel", help="Skip wheel build"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip MCP host build"),
    no_wire: bool = typer.Option(
        False, "--no-wire", help="Skip connectors.yaml / sample.env / ALL_PACKAGES"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing connector / MCP output"),
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
    from nw_connector_builder.pipeline import BuildError, UsageError, run_build

    tool_mode = _tool_mode_or_exit(tool_search, full_tool_list)
    node_wire_root = _root()
    progress = GenerateProgress()
    if no_wheel:
        progress.mark_skipped("wheel")
    if no_mcp:
        progress.mark_skipped("mcp")
    if no_wire:
        progress.mark_skipped("wire")

    try:
        with progress:

            def _connector() -> int:
                code = run_build(
                    spec=path,
                    connector_id=id,
                    node_wire_root=node_wire_root,
                    wire=not no_wire,
                    force=force,
                    no_mcp=True,
                )
                if code != 0:
                    raise StageError(f"Connector build returned exit code {code}")
                return code

            progress.run_stage("connector", _connector)

            chosen_mode = None
            if not no_mcp:
                # Needs the generated source, and comes before the slow wheel
                # builds so an over-budget question is not kept waiting.
                decision = decide_tool_mode(
                    node_wire_root,
                    id,
                    tool_mode=tool_mode,
                    max_tool_listing_kb=max_tool_listing_kb,
                    console=progress.console,
                    notify=lambda message: progress.log(f"warning: {message}"),
                    pause=progress.paused,
                )
                chosen_mode = decision.mode
                progress.log(describe_decision(decision))

            if not no_wheel:

                def _wheels() -> None:
                    # MCP hosts need runtime + bindings + connector wheels.
                    run_wheel_build(node_wire_root, runtime=True, bindings=True, log=progress.log)
                    run_wheel_build(node_wire_root, connector_id=id, log=progress.log)

                progress.run_stage("wheel", _wheels)

            if not no_mcp:

                def _mcp() -> Path:
                    if not wheels_present(node_wire_root, id):

                        def _build_missing() -> None:
                            run_wheel_build(
                                node_wire_root,
                                runtime=True,
                                bindings=True,
                                log=progress.log,
                            )
                            run_wheel_build(
                                node_wire_root,
                                connector_id=id,
                                log=progress.log,
                            )

                        ensure(
                            False,
                            prompt=(
                                f"Wheels missing for '{id}' "
                                f"(packages/runtime|bindings/dist or "
                                f"packages/connectors/{id}/dist) — build now?"
                            ),
                            fix_command=(
                                f"nw gen-whl --runtime --bindings && nw gen-whl --connector-id {id}"
                            ),
                            build_fn=_build_missing,
                        )
                    return run_mcp_build(
                        node_wire_root, id, force_output=force, tool_mode=chosen_mode
                    )

                progress.run_stage("mcp", _mcp)

            if not no_wire:
                progress.run_stage(
                    "wire",
                    lambda: register_all_packages(node_wire_root, id),
                )
    except (
        BuildError,
        UsageError,
        StageError,
        FileNotFoundError,
        FileExistsError,
        ValueError,
        RuntimeError,
    ) as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("gen-whl")
def gen_whl(
    id: Optional[str] = typer.Option(
        None, "--connector-id", help="Connector id (required unless --runtime/--bindings)"
    ),
    host: bool = typer.Option(False, "--host", help="Host-only wheel build"),
    all_: bool = typer.Option(False, "--all", help="Full cibuildwheel matrix"),
    runtime: bool = typer.Option(False, "--runtime", help="Build packages/runtime"),
    bindings: bool = typer.Option(
        False, "--bindings", help="Build packages/bindings (MCP host surface)"
    ),
) -> None:
    """Build binary wheels via scripts/build-packages.sh (Linux-only by default)."""
    node_wire_root = _root()

    if host and all_:
        err_console.print(
            "[bold #e01d5a]error:[/bold #e01d5a] --host and --all are mutually exclusive"
        )
        raise typer.Exit(2)

    if not runtime and not bindings and not id:
        err_console.print(
            "[bold #e01d5a]error:[/bold #e01d5a] --connector-id is required "
            "unless --runtime and/or --bindings is set"
        )
        raise typer.Exit(2)

    try:
        with console.status("[bold]Building wheels…[/bold]", spinner="dots"):
            targets: list[str] = []
            if runtime or bindings:
                run_wheel_build(
                    node_wire_root,
                    runtime=runtime,
                    bindings=bindings,
                    host=host,
                    all_=all_,
                )
                if runtime:
                    targets.append("packages/runtime")
                if bindings:
                    targets.append("packages/bindings")
            if id:
                run_wheel_build(
                    node_wire_root,
                    connector_id=id,
                    host=host,
                    all_=all_,
                )
                targets.append(f"packages/connectors/{id}")
        console.print(f"[green]Wheel build OK[/green] ({', '.join(targets)})")
    except StageError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("gen-mcp")
def gen_mcp(
    id: str = typer.Option(..., "--connector-id", help="Connector id"),
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
    tool_mode = _tool_mode_or_exit(tool_search, full_tool_list)
    node_wire_root = _root()

    if not runtime_wheel_present(node_wire_root):
        ensure(
            False,
            prompt="Runtime wheel not found in packages/runtime/dist/ — build it now?",
            fix_command="nw gen-whl --runtime",
            build_fn=lambda: run_wheel_build(node_wire_root, runtime=True),
        )

    if not bindings_wheel_present(node_wire_root):
        ensure(
            False,
            prompt="Bindings wheel not found in packages/bindings/dist/ — build it now?",
            fix_command="nw gen-whl --bindings",
            build_fn=lambda: run_wheel_build(node_wire_root, bindings=True),
        )

    if not connector_wheel_present(node_wire_root, id):
        ensure(
            False,
            prompt=(f"Connector wheel not found in packages/connectors/{id}/dist/ — build it now?"),
            fix_command=f"nw gen-whl --connector-id {id}",
            build_fn=lambda: run_wheel_build(node_wire_root, connector_id=id),
        )

    try:
        # Before the spinner: the over-budget question needs the terminal.
        decision = decide_tool_mode(
            node_wire_root,
            id,
            tool_mode=tool_mode,
            max_tool_listing_kb=max_tool_listing_kb,
            console=console,
            notify=lambda message: err_console.print(
                f"[bold #ecb32e]warning:[/bold #ecb32e] {message}", highlight=False
            ),
        )
        console.print(describe_decision(decision), highlight=False)
        with console.status("[bold]Building MCP host…[/bold]", spinner="dots"):
            project = run_mcp_build(
                node_wire_root, id, force_output=force_output, tool_mode=decision.mode
            )
        console.print(f"[green]MCP host ready[/green]: {project}")
    except (StageError, FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("docker-build")
def docker_build(
    id: str = typer.Option(..., "--connector-id", help="Connector id"),
    tag: str = typer.Option("latest", "--tag", help="Docker image tag"),
) -> None:
    """Build a Docker image from the generated MCP host project."""
    node_wire_root = _root()
    project = mcp_project_dir(node_wire_root, id)

    ensure(
        project.is_dir(),
        prompt=f"MCP project not found at {project} — generate it now?",
        fix_command=f"nw gen-mcp --connector-id {id}",
        build_fn=lambda: run_mcp_build(node_wire_root, id, force_output=True),
    )

    try:
        with console.status("[bold]docker build…[/bold]", spinner="dots"):
            image = run_docker_build(node_wire_root, id, tag=tag)
        console.print(f"[green]Image ready[/green]: {image}")
    except StageError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("gen-stacklok")
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
    from nw_cli.stacklok import prepare_spec, prepared_spec_path

    if (scope is None) == (path is None):
        _usage_error("pass exactly one of --path or --scope")
    if path is not None and not id:
        _usage_error("--path needs --connector-id")
    node_wire_root = _root()

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
            "musllinux wheels (cp313)",
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
    progress = GenerateProgress(stages=stages)
    if no_wheel:
        progress.mark_skipped("wheel")

    try:
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
                if reuse:
                    progress.log(f"Reusing {existing} (pass --rescope to redo AI scoping)")
                request = ScopingRequest(
                    spec=prepared,
                    connector_id=id,
                    workflows=list(workflow or []),
                    auth_hint=auth_hint,
                    notes=scoping_notes,
                )
                interactive = not headless and is_interactive()

                def _scoping() -> Path:
                    if interactive:
                        with progress.paused():
                            console.print(
                                "\n[bold]Phase 1: AI scoping[/bold]: opening Claude Code with "
                                "/ai-scoping. Answer its questions; exit (/exit) when it's done.\n",
                                highlight=False,
                            )
                            return run_ai_scoping_interactive(
                                node_wire_root, request, work_dir=work_dir, model=scoping_model
                            )
                    return run_ai_scoping(
                        node_wire_root,
                        request,
                        work_dir=work_dir,
                        model=scoping_model,
                        log=progress.log,
                    )

                produced = progress.run_stage("scoping", _scoping)
                scope_file = produced or existing
                if not progress.run_stage(
                    "review", lambda: _review(progress, scope_file, work_dir)
                ):
                    console.print(
                        f"\nStopped for review. Edit {scope_file}"
                        f" (reasoning: {work_dir / 'scoping-summary.md'}), then run:\n"
                        f"  uv run nw gen-stacklok --scope {scope_file} --connector-id {id}",
                        highlight=False,
                        soft_wrap=True,
                    )
                    return
                project = _stacklok_phase3(
                    progress, node_wire_root, scope_file, id, output_dir, force, no_lock
                )
        else:
            assert scope_file is not None
            with progress:
                project = _stacklok_phase3(
                    progress, node_wire_root, scope_file, id, output_dir, force, no_lock
                )
        console.print(f"[green]MCP server ready[/green]: {project}")
        console.print(_run_instructions(project), highlight=False, soft_wrap=True)
    except typer.Exit:
        raise
    except (
        BuildError,
        UsageError,
        StageError,
        FileNotFoundError,
        FileExistsError,
        ValueError,
        RuntimeError,
    ) as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


def _run_instructions(project: Path) -> str:
    """Build, run and ToolHive commands for a generated stacklok project (its real names)."""
    image = project.name  # <server>-mcp
    return (
        "\nNext, run it locally behind ToolHive (single tenant; ToolHive adds the bearer token):\n"
        f"  docker build -t {image} {project}\n"
        f"  docker rm -f {image} 2>/dev/null; docker run -d --name {image} -p 8200:8100 "
        f"-e NW_MULTITENANCY_ENABLED=false {image}\n"
        f"  thv run http://127.0.0.1:8200/mcp --name {image} --transport streamable-http "
        "--remote-auth-bearer-token <upstream-token>\n"
        f"Kubernetes: {project / 'deploy' / 'README.md'}"
    )


def _usage_error(message: str) -> None:
    err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {message}")
    raise typer.Exit(2)


def _review(progress: GenerateProgress, scope_file: Path, work_dir: Path) -> bool:
    """Phase 2: the human reviews the scope. True: continue to Phase 3; False: stop to edit."""
    from rich.prompt import Confirm

    summary = work_dir / "scoping-summary.md"
    if not is_interactive():
        progress.log("Phase 2 needs a human review and there is no terminal; stopping.")
        return False
    with progress.paused():
        console.print(
            "\n[bold]Phase 2: human review[/bold]\n"
            f"  scope:   {scope_file}\n"
            f"  summary: {summary if summary.is_file() else '(none written)'}"
            " (the AI's reasoning, and gates it auto-approved)",
            highlight=False,
            soft_wrap=True,
        )
        return Confirm.ask(
            "Is the scope OK? [bold]y[/bold] = generate now, [bold]n[/bold] = stop so you can edit it",
            default=True,
        )


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
) -> Path:
    """Phase 3: connector → musllinux wheels → stacklok generator on node-wire."""
    from nw_connector_builder.pipeline import run_build

    from nw_cli.stacklok import (
        default_output_dir,
        read_scope,
        run_stacklok_generate,
        run_stacklok_wheel_build,
    )

    scoped = read_scope(scope_file, connector_id)
    out = output_dir or default_output_dir(node_wire_root)
    replace_output = force or _confirm_replace(progress, out / f"{scoped.server_name}-mcp")

    def _connector() -> None:
        # Always regenerated: the scope's endpoints must map onto a connector built from this
        # very spec. nw-connector-builder still refuses to overwrite a hand-written connector.
        code = run_build(
            spec=scoped.connector_spec_source,
            connector_id=scoped.connector_id,
            node_wire_root=node_wire_root,
            wire=True,
            force=True,
            no_mcp=True,
            base_url=scoped.base_url,
        )
        if code != 0:
            raise StageError(f"Connector build returned exit code {code}")

    progress.run_stage("connector", _connector)
    progress.run_stage(
        "wheel",
        lambda: run_stacklok_wheel_build(node_wire_root, scoped.connector_id, log=progress.log),
    )
    with tempfile.TemporaryDirectory(prefix="nw-stacklok-") as work:
        return progress.run_stage(
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


if __name__ == "__main__":
    app()
