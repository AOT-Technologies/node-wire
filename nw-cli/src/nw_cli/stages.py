# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Stage implementations: wheel build, MCP host, docker, ALL_PACKAGES."""

from __future__ import annotations

import os
import subprocess  # nosec B404  # local CLI pipeline; Popen below uses an arg list, no shell=True
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from nw_cli.names import mcp_project_dir
from nw_cli.prerequisites import require_docker
from nw_cli.wheel_cache import (
    WheelState,
    package_source_hash,
    stamp_build,
    wheel_state,
)

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


def stale_wheel_packages(node_wire_root: Path, packages: Sequence[str]) -> list[str]:
    """The packages whose wheel is not ``FRESH`` (missing, or not built from these sources)."""
    return [p for p in packages if wheel_state(node_wire_root, p) is not WheelState.FRESH]


def describe_wheel_states(node_wire_root: Path, packages: Sequence[str]) -> str:
    """``packages/runtime (stale), ...`` for the packages that are not ``FRESH``."""
    return ", ".join(
        f"{p} ({wheel_state(node_wire_root, p).value})"
        for p in stale_wheel_packages(node_wire_root, packages)
    )


def run_wheel_build(
    node_wire_root: Path,
    *,
    connector_id: str | None = None,
    runtime: bool = False,
    bindings: bool = False,
    packages: Sequence[str] | None = None,
    log: LogFn | None = None,
) -> dict[str, Path]:
    """Build wheels with one ``scripts/build-packages.sh`` run; returns ``{package: wheel}``.

    Targets are ``packages`` when given, else the runtime / bindings / connector selection.
    Each package that builds gets a source stamp (see :mod:`nw_mcp_builder.wheel_cache`), so
    :func:`stale_wheel_packages` can skip it next time while its sources are unchanged; a
    failed package is named in the error and the others keep their stamps.
    """
    targets = (
        list(packages)
        if packages is not None
        else wheel_packages(connector_id=connector_id, runtime=runtime, bindings=bindings)
    )
    if not targets:
        raise StageError("--connector-id is required unless --runtime or --bindings is set")

    script = node_wire_root / "scripts" / "build-packages.sh"
    if not script.is_file():
        raise StageError(f"build-packages.sh not found: {script}")
    absent = [t for t in targets if not (node_wire_root / t).is_dir()]
    if absent:
        raise StageError(
            f"No such package: {', '.join(absent)} (generate the connector first: nw gen-all)"
        )
    # Hashed before the build: an edit made while it runs leaves that wheel stale.
    digests = {t: package_source_hash(node_wire_root, t) for t in targets}
    # Relative POSIX path: cwd is node_wire_root. An absolute Windows path
    # (G:\...) is eaten by Git Bash as escape sequences (exit 127).
    cmd = ["bash", "scripts/build-packages.sh", *targets]
    started = time.time()
    try:
        code = run_logged_command(cmd, cwd=node_wire_root, log=log)
    finally:
        # Even after a failure or Ctrl-C: every package that did build keeps its stamp.
        built = _stamp_built(node_wire_root, digests, since=started)
    failed = [t for t in targets if t not in built]
    if code != 0 or failed:
        raise StageError(
            f"Wheel build failed (exit {code}) for: {', '.join(failed) or 'none listed'}"
            f" ({' '.join(cmd)})"
        )
    return built


def _stamp_built(node_wire_root: Path, digests: dict[str, str], *, since: float) -> dict[str, Path]:
    """Stamp each package whose build wrote a pure wheel after ``since``; returns those."""
    built: dict[str, Path] = {}
    for package, digest in digests.items():
        dist = node_wire_root / package / "dist"
        written = [w for w in dist.glob("*.whl") if w.stat().st_mtime >= since - 1]
        if not written:
            continue  # not reached, or failed (build-packages.sh deletes old wheels first)
        try:
            built[package] = stamp_build(node_wire_root, package, digest)
        except FileNotFoundError:
            continue
    return built


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
    # Imported here: it loads pydantic and the generator models, which most ``nw``
    # commands (and ``--help``) never need.
    from nw_mcp_builder.from_connector import run_from_connector
    from nw_mcp_builder.wheel_cache import WheelsNotFreshError

    package_root = node_wire_root / "nw-mcp-builder"
    try:
        return run_from_connector(
            connector_id,
            node_wire_root=node_wire_root,
            package_root=package_root,
            skip_build_wheels=True,
            force_output=force_output,
            tool_mode=tool_mode,
        )
    except WheelsNotFreshError as exc:
        flags = " ".join(
            "--runtime"
            if p == "packages/runtime"
            else "--bindings"
            if p == "packages/bindings"
            else f"--connector-id {connector_id}"
            for p in exc.packages
        )
        raise StageError(f"{exc}. Build them first: uv run nw gen-whl {flags}") from exc


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
    return build_image(project, f"{project.name}:{tag}", log=log)


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


def register_all_packages(node_wire_root: Path, connector_id: str) -> bool:
    """Insert ``packages/connectors/<id>`` into ``ALL_PACKAGES`` if missing (True if added)."""
    from nw_connector_builder.wire import WireError
    from nw_connector_builder.wire import register_all_packages as _register

    try:
        return _register(node_wire_root, connector_id)
    except WireError as exc:
        raise StageError(str(exc)) from exc


def run_wire(node_wire_root: Path, connector_id: str) -> str:
    """Wire a built connector in (connectors.yaml, sample.env, ALL_PACKAGES); idempotent.

    Returns what happened to ``ALL_PACKAGES`` (``added``, ``present``, ``skipped (...)``).
    """
    from nw_connector_builder.wire import WireError, wire_connector_from_report

    try:
        return str(wire_connector_from_report(node_wire_root, connector_id)["all_packages"])
    except WireError as exc:
        raise StageError(str(exc)) from exc
