# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Typer CLI for ``nw`` — generate / wheel / mcp / docker-build."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

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

app = typer.Typer(
    name="nw",
    help="Node Wire CLI — OpenAPI connector → wheel → MCP → Docker",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


def _root() -> Path:
    try:
        return resolve_node_wire_root()
    except RootError as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("generate")
def generate(
    id: str = typer.Option(..., "--id", help="Connector id (e.g. pet_store)"),
    path: str = typer.Option(..., "--path", help="OpenAPI/Swagger spec path or URL"),
    no_wheel: bool = typer.Option(False, "--no-wheel", help="Skip wheel build"),
    no_mcp: bool = typer.Option(False, "--no-mcp", help="Skip MCP host build"),
    no_wire: bool = typer.Option(
        False, "--no-wire", help="Skip connectors.yaml / sample.env / ALL_PACKAGES"
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing connector / MCP output"
    ),
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
                    lambda: run_wheel_build(node_wire_root, connector_id=id),
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
                            fix_command=f"nw wheel --id {id}",
                            build_fn=lambda: run_wheel_build(
                                node_wire_root, connector_id=id
                            ),
                        )
                        if not runtime_wheel_present(node_wire_root):
                            ensure(
                                False,
                                prompt="Runtime wheel still missing — build it now?",
                                fix_command="nw wheel --runtime",
                                build_fn=lambda: run_wheel_build(
                                    node_wire_root, runtime=True
                                ),
                            )
                    return run_mcp_build(
                        node_wire_root, id, force_output=force
                    )

                progress.run_stage("mcp", _mcp)

            if not no_wire:
                progress.run_stage(
                    "wire",
                    lambda: register_all_packages(node_wire_root, id),
                )
    except (BuildError, UsageError, StageError, FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("wheel")
def wheel(
    id: Optional[str] = typer.Option(
        None, "--id", help="Connector id (required unless --runtime)"
    ),
    host: bool = typer.Option(False, "--host", help="Host-only wheel build"),
    all_: bool = typer.Option(False, "--all", help="Full cibuildwheel matrix"),
    runtime: bool = typer.Option(
        False, "--runtime", help="Build only packages/runtime"
    ),
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
            "[bold #e01d5a]error:[/bold #e01d5a] --id is required unless --runtime is set"
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


@app.command("mcp")
def mcp(
    id: str = typer.Option(..., "--id", help="Connector id"),
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
            fix_command="nw wheel --runtime",
            build_fn=lambda: run_wheel_build(node_wire_root, runtime=True),
        )

    if not connector_wheel_present(node_wire_root, id):
        ensure(
            False,
            prompt=(
                f"Connector wheel not found in packages/connectors/{id}/dist/ "
                "— build it now?"
            ),
            fix_command=f"nw wheel --id {id}",
            build_fn=lambda: run_wheel_build(node_wire_root, connector_id=id),
        )

    try:
        with console.status("[bold]Building MCP host…[/bold]", spinner="dots"):
            project = run_mcp_build(
                node_wire_root, id, force_output=force_output
            )
        console.print(f"[green]MCP host ready[/green]: {project}")
    except (StageError, FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        err_console.print(f"[bold #e01d5a]error:[/bold #e01d5a] {exc}")
        raise typer.Exit(1) from exc


@app.command("docker-build")
def docker_build(
    id: str = typer.Option(..., "--id", help="Connector id"),
    tag: str = typer.Option("latest", "--tag", help="Docker image tag"),
) -> None:
    """Build a Docker image from the generated MCP host project."""
    node_wire_root = _root()
    project = mcp_project_dir(node_wire_root, id)

    ensure(
        project.is_dir(),
        prompt=f"MCP project not found at {project} — generate it now?",
        fix_command=f"nw mcp --id {id}",
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
