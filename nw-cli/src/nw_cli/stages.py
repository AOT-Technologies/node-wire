# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Stage implementations: wheel build, MCP host, docker, ALL_PACKAGES."""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404  # local CLI pipeline; Popen below uses an arg list, no shell=True
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from nw_mcp_builder.from_connector import run_from_connector

from nw_cli.names import docker_image_tag, mcp_project_dir
from nw_cli.prerequisites import require_docker
from nw_cli.wheel_cache import is_fresh, record_build

LogFn = Callable[[str], None]


class StageError(Exception):
    """A pipeline stage failed."""


def run_logged_command(
    cmd: list[str],
    *,
    cwd: Path,
    log: LogFn | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | None = None,
) -> int:
    """Run *cmd*, streaming combined stdout/stderr line-by-line through *log*.

    When *log* is None, lines go to the process stdout (for standalone commands).
    Callers that own a live Progress must pass ``progress.log`` so output stays
    above the bars instead of writing to the raw TTY.
    """
    try:
        proc = subprocess.Popen(  # nosec B603  # arg list from trusted local pipeline callers, no shell
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=dict(env) if env is not None else None,
            stdin=stdin,
        )
    except FileNotFoundError as exc:
        raise StageError(f"`{cmd[0]}` not found on PATH; install it and retry") from exc
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            text = line.rstrip("\n")
            if log is not None:
                log(text)
            else:
                print(text, flush=True)
        return proc.wait()
    except BaseException:
        # Ctrl-C or a failing log callback: do not leave the build running in the background.
        _stop(proc)
        raise


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def wheels_present(node_wire_root: Path, connector_id: str) -> bool:
    """True when runtime, bindings, and connector ``dist/`` each have a ``.whl``."""
    return (
        runtime_wheel_present(node_wire_root)
        and bindings_wheel_present(node_wire_root)
        and connector_wheel_present(node_wire_root, connector_id)
    )


def runtime_wheel_present(node_wire_root: Path) -> bool:
    return bool(list((node_wire_root / "packages" / "runtime" / "dist").glob("*.whl")))


def bindings_wheel_present(node_wire_root: Path) -> bool:
    return bool(list((node_wire_root / "packages" / "bindings" / "dist").glob("*.whl")))


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


def wheel_packages(
    *, connector_id: str | None = None, runtime: bool = False, bindings: bool = False
) -> list[str]:
    """``build-packages.sh`` targets for the given selection, in build order."""
    packages = []
    if runtime:
        packages.append("packages/runtime")
    if bindings:
        packages.append("packages/bindings")
    if connector_id:
        packages.append(f"packages/connectors/{connector_id}")
    return packages


def mcp_host_packages(connector_id: str) -> list[str]:
    """Packages whose wheels a generated MCP host bundles."""
    return wheel_packages(connector_id=connector_id, runtime=True, bindings=True)


def stale_wheel_packages(
    node_wire_root: Path, packages: Sequence[str], *, host: bool = False, all_: bool = False
) -> list[str]:
    """The packages whose wheels for this build mode are missing or older than their sources."""
    key = build_mode_flag(host=host, all_=all_).lstrip("-")
    return [p for p in packages if not is_fresh(node_wire_root, p, key)]


def run_wheel_build(
    node_wire_root: Path,
    *,
    connector_id: str | None = None,
    runtime: bool = False,
    bindings: bool = False,
    packages: Sequence[str] | None = None,
    host: bool = False,
    all_: bool = False,
    log: LogFn | None = None,
) -> None:
    """Build wheels with one ``scripts/build-packages.sh`` run.

    Targets are ``packages`` when given, else the runtime / bindings / connector selection.
    Each built package gets a source stamp, so :func:`stale_wheel_packages` can skip it next
    time while its sources are unchanged.
    """
    targets = (
        list(packages)
        if packages is not None
        else wheel_packages(connector_id=connector_id, runtime=runtime, bindings=bindings)
    )
    if not targets:
        raise StageError("--connector-id is required unless --runtime or --bindings is set")

    mode = build_mode_flag(host=host, all_=all_)
    script = node_wire_root / "scripts" / "build-packages.sh"
    if not script.is_file():
        raise StageError(f"build-packages.sh not found: {script}")
    absent = [t for t in targets if not (node_wire_root / t).is_dir()]
    if absent:
        raise StageError(
            f"No such package: {', '.join(absent)} (generate the connector first: nw gen-all)"
        )
    if mode != "--host-only":
        require_docker(f"{mode} wheel builds")

    # Relative POSIX path: cwd is node_wire_root. An absolute Windows path
    # (G:\...) is eaten by Git Bash as escape sequences (exit 127).
    cmd = ["bash", "scripts/build-packages.sh", mode, *targets]
    started = time.time()
    code = run_logged_command(cmd, cwd=node_wire_root, log=log)
    if code != 0:
        raise StageError(f"Wheel build failed (exit {code}): {' '.join(cmd)}")
    for package in targets:
        record_build(node_wire_root, package, mode.lstrip("-"), since=started)


def run_mcp_build(
    node_wire_root: Path,
    connector_id: str,
    *,
    force_output: bool = False,
    tool_mode: str | None = None,
) -> Path:
    """Call ``run_from_connector`` with ``skip_build_wheels=True``.

    ``tool_mode`` is decided by the caller first (``tool_mode.decide_tool_mode``),
    so nw-mcp-builder does not ask again.
    """
    package_root = node_wire_root / "nw-mcp-builder"
    return run_from_connector(
        connector_id,
        node_wire_root=node_wire_root,
        package_root=package_root,
        skip_build_wheels=True,
        force_output=force_output,
        tool_mode=tool_mode,
    )


def run_docker_build(
    node_wire_root: Path,
    connector_id: str,
    *,
    tag: str = "latest",
    log: LogFn | None = None,
) -> str:
    """Build the generated MCP image with BuildKit enabled.

    Always sets ``DOCKER_BUILDKIT=1`` so Dockerfile ``RUN --mount=type=cache``
    works. When ``NW_DOCKER_CACHE_FROM`` / ``NW_DOCKER_CACHE_TO`` are set (e.g.
    ``type=gha,scope=…`` in CI), uses ``docker buildx build --load`` with those
    cache backends; otherwise plain ``docker build``.
    """
    project = mcp_project_dir(node_wire_root, connector_id)
    if not project.is_dir():
        raise StageError(f"MCP project directory not found: {project}")
    return build_image(project, docker_image_tag(connector_id, tag), log=log)


def build_image(project: Path, image: str, *, log: LogFn | None = None) -> str:
    """``docker build`` of any generated project directory (see :func:`run_docker_build`)."""
    if not (project / "Dockerfile").is_file():
        raise StageError(f"No Dockerfile in {project}")
    require_docker("docker-build")
    env = os.environ.copy()
    env["DOCKER_BUILDKIT"] = "1"

    cache_from = env.get("NW_DOCKER_CACHE_FROM", "").strip()
    cache_to = env.get("NW_DOCKER_CACHE_TO", "").strip()
    if cache_from or cache_to:
        cmd = ["docker", "buildx", "build", "--load", "-t", image]
        if cache_from:
            cmd.extend(["--cache-from", cache_from])
        if cache_to:
            cmd.extend(["--cache-to", cache_to])
        cmd.append(".")
    else:
        cmd = ["docker", "build", "-t", image, "."]

    code = run_logged_command(cmd, cwd=project, log=log, env=env)
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
