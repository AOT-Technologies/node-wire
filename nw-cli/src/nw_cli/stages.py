# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Stage implementations: wheel build, MCP host, docker, ALL_PACKAGES."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from nw_mcp_builder.from_connector import run_from_connector

from nw_cli.names import docker_image_tag, mcp_project_dir

LogFn = Callable[[str], None]


class StageError(Exception):
    """A pipeline stage failed."""


def run_logged_command(
    cmd: list[str],
    *,
    cwd: Path,
    log: LogFn | None = None,
) -> int:
    """Run *cmd*, streaming combined stdout/stderr line-by-line through *log*.

    When *log* is None, lines go to the process stdout (for standalone commands).
    Callers that own a live Progress must pass ``progress.log`` so output stays
    above the bars instead of writing to the raw TTY.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip("\n")
        if log is not None:
            log(text)
        else:
            print(text, flush=True)
    return proc.wait()


def wheels_present(node_wire_root: Path, connector_id: str) -> bool:
    """True when runtime and connector ``dist/`` each contain at least one ``.whl``."""
    runtime_dist = node_wire_root / "packages" / "runtime" / "dist"
    connector_dist = node_wire_root / "packages" / "connectors" / connector_id / "dist"
    return bool(list(runtime_dist.glob("*.whl"))) and bool(list(connector_dist.glob("*.whl")))


def runtime_wheel_present(node_wire_root: Path) -> bool:
    return bool(list((node_wire_root / "packages" / "runtime" / "dist").glob("*.whl")))


def connector_wheel_present(node_wire_root: Path, connector_id: str) -> bool:
    return bool(
        list((node_wire_root / "packages" / "connectors" / connector_id / "dist").glob("*.whl"))
    )


def build_mode_flag(*, host: bool = False, all_: bool = False) -> str:
    """Return the build-packages.sh mode flag (default Linux-only)."""
    if host and all_:
        raise StageError("--host and --all are mutually exclusive")
    if host:
        return "--host-only"
    if all_:
        return "--all"
    return "--linux-only"


def run_wheel_build(
    node_wire_root: Path,
    *,
    connector_id: str | None = None,
    runtime: bool = False,
    host: bool = False,
    all_: bool = False,
    log: LogFn | None = None,
) -> None:
    """Subprocess ``scripts/build-packages.sh`` for connector and/or runtime."""
    if not runtime and not connector_id:
        raise StageError("--connector-id is required unless --runtime is set")

    mode = build_mode_flag(host=host, all_=all_)
    script = node_wire_root / "scripts" / "build-packages.sh"
    if not script.is_file():
        raise StageError(f"build-packages.sh not found: {script}")

    # Spec: --runtime builds only packages/runtime (not bundled with connector).
    if runtime:
        targets = ["packages/runtime"]
    else:
        targets = [f"packages/connectors/{connector_id}"]

    cmd = ["bash", str(script), mode, *targets]
    code = run_logged_command(cmd, cwd=node_wire_root, log=log)
    if code != 0:
        raise StageError(f"Wheel build failed (exit {code}): {' '.join(cmd)}")


def run_mcp_build(
    node_wire_root: Path,
    connector_id: str,
    *,
    force_output: bool = False,
) -> Path:
    """Call ``run_from_connector`` with ``skip_build_wheels=True``."""
    package_root = node_wire_root / "nw-mcp-builder"
    return run_from_connector(
        connector_id,
        node_wire_root=node_wire_root,
        package_root=package_root,
        skip_build_wheels=True,
        force_output=force_output,
    )


def run_docker_build(
    node_wire_root: Path,
    connector_id: str,
    *,
    tag: str = "latest",
    log: LogFn | None = None,
) -> str:
    """``docker build -t <server>-mcp:<tag> .`` inside the generated project dir."""
    project = mcp_project_dir(node_wire_root, connector_id)
    if not project.is_dir():
        raise StageError(f"MCP project directory not found: {project}")

    image = docker_image_tag(connector_id, tag)
    cmd = ["docker", "build", "-t", image, "."]
    code = run_logged_command(cmd, cwd=project, log=log)
    if code != 0:
        raise StageError(f"docker build failed (exit {code}): {image}")
    return image


_ALL_PACKAGES_RE = re.compile(
    r"(ALL_PACKAGES=\(\n)(.*?)(\n\))",
    re.DOTALL,
)


def register_all_packages(node_wire_root: Path, connector_id: str) -> bool:
    """Insert ``packages/connectors/<id>`` into ``ALL_PACKAGES`` if missing.

    Returns True if the file was modified.
    """
    script = node_wire_root / "scripts" / "build-packages.sh"
    text = script.read_text(encoding="utf-8")
    entry = f"packages/connectors/{connector_id}"

    match = _ALL_PACKAGES_RE.search(text)
    if not match:
        raise StageError(f"ALL_PACKAGES block not found in {script}")

    body = match.group(2)
    # Already present as a whole line entry
    for line in body.splitlines():
        if line.strip() == entry:
            return False

    # Preserve indentation from existing connector lines (2 spaces)
    indent = "  "
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("packages/connectors/"):
            indent = line[: len(line) - len(stripped)]
            break

    new_body = body.rstrip("\n") + f"\n{indent}{entry}"
    new_text = (
        text[: match.start()] + match.group(1) + new_body + match.group(3) + text[match.end() :]
    )
    script.write_text(new_text, encoding="utf-8")
    return True
