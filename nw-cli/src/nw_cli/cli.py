# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Typer CLI for ``nw`` — gen-all / gen-whl / gen-mcp / docker-build."""

from __future__ import annotations

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
from nw_cli.prerequisites import ensure
from nw_cli.progress import GenerateProgress
from nw_cli.root import RootError, resolve_node_wire_root
from nw_cli.stages import (
    StageError,
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
) -> None:
    """One-shot: connector codegen → wheel → MCP host → wire."""
    from nw_connector_builder.pipeline import BuildError, UsageError, run_build

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

            if not no_wheel:
                progress.run_stage(
                    "wheel",
                    lambda: run_wheel_build(node_wire_root, connector_id=id, log=progress.log),
                )

            if not no_mcp:

                def _mcp() -> Path:
                    if not wheels_present(node_wire_root, id):
                        ensure(
                            False,
                            prompt=(
                                f"Wheels missing for '{id}' "
                                f"(packages/runtime/dist or "
                                f"packages/connectors/{id}/dist) — build now?"
                            ),
                            fix_command=f"nw gen-whl --connector-id {id}",
                            build_fn=lambda: run_wheel_build(
                                node_wire_root,
                                connector_id=id,
                                log=progress.log,
                            ),
                        )
                        if not runtime_wheel_present(node_wire_root):
                            ensure(
                                False,
                                prompt="Runtime wheel still missing — build it now?",
                                fix_command="nw gen-whl --runtime",
                                build_fn=lambda: run_wheel_build(
                                    node_wire_root,
                                    runtime=True,
                                    log=progress.log,
                                ),
                            )
                    return run_mcp_build(node_wire_root, id, force_output=force)

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
        None, "--connector-id", help="Connector id (required unless --runtime)"
    ),
    host: bool = typer.Option(False, "--host", help="Host-only wheel build"),
    all_: bool = typer.Option(False, "--all", help="Full cibuildwheel matrix"),
    runtime: bool = typer.Option(False, "--runtime", help="Build only packages/runtime"),
) -> None:
    """Build binary wheels via scripts/build-packages.sh (Linux-only by default)."""
    node_wire_root = _root()

    if host and all_:
        err_console.print(
            "[bold #e01d5a]error:[/bold #e01d5a] --host and --all are mutually exclusive"
        )
        raise typer.Exit(2)

    if not runtime and not id:
        err_console.print(
            "[bold #e01d5a]error:[/bold #e01d5a] --connector-id is required unless --runtime is set"
        )
        raise typer.Exit(2)

    try:
        with console.status("[bold]Building wheels…[/bold]", spinner="dots"):
            run_wheel_build(
                node_wire_root,
                connector_id=id,
                runtime=runtime,
                host=host,
                all_=all_,
            )
        target = "packages/runtime" if runtime else f"packages/connectors/{id}"
        console.print(f"[green]Wheel build OK[/green] ({target})")
    except StageError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("gen-mcp")
def gen_mcp(
    id: str = typer.Option(..., "--connector-id", help="Connector id"),
    force_output: bool = typer.Option(
        False, "--force-output", help="Replace existing out/<server>-mcp/"
    ),
) -> None:
    """Build MCP host from an existing connector (requires wheels)."""
    node_wire_root = _root()

    if not runtime_wheel_present(node_wire_root):
        ensure(
            False,
            prompt="Runtime wheel not found in packages/runtime/dist/ — build it now?",
            fix_command="nw gen-whl --runtime",
            build_fn=lambda: run_wheel_build(node_wire_root, runtime=True),
        )

    if not connector_wheel_present(node_wire_root, id):
        ensure(
            False,
            prompt=(f"Connector wheel not found in packages/connectors/{id}/dist/ — build it now?"),
            fix_command=f"nw gen-whl --connector-id {id}",
            build_fn=lambda: run_wheel_build(node_wire_root, connector_id=id),
        )

    try:
        with console.status("[bold]Building MCP host…[/bold]", spinner="dots"):
            project = run_mcp_build(node_wire_root, id, force_output=force_output)
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


if __name__ == "__main__":
    app()
