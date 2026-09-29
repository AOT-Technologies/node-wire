# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""CLI tests for nw-cli (mocked stages)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from typer.testing import CliRunner

from nw_cli.cli import app
from nw_cli.stages import register_all_packages

runner = CliRunner()


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """Minimal node-wire layout for root resolution."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "build-packages.sh").write_text(
        "#!/bin/bash\nALL_PACKAGES=(\n  packages/runtime\n  packages/connectors/slack\n)\n",
        encoding="utf-8",
    )
    (tmp_path / "nw-mcp-builder").mkdir()
    (tmp_path / "packages" / "runtime" / "dist").mkdir(parents=True)
    (tmp_path / "packages" / "connectors" / "pet_store" / "dist").mkdir(parents=True)
    return tmp_path


def test_wheel_invokes_build_packages_linux_only(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.run_wheel_build") as wheel,
    ):
        result = runner.invoke(app, ["gen-whl", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    wheel.assert_called_once_with(
        fake_root,
        packages=["packages/connectors/pet_store"],
        host=False,
        all_=False,
        log=ANY,
    )


def test_wheel_runtime_and_host_flags(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.run_wheel_build") as wheel,
    ):
        result = runner.invoke(app, ["gen-whl", "--runtime", "--host"])
    assert result.exit_code == 0, result.output
    wheel.assert_called_once_with(
        fake_root, packages=["packages/runtime"], host=True, all_=False, log=ANY
    )


def test_wheel_runtime_and_connector_build_in_one_run(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.run_wheel_build") as wheel,
    ):
        result = runner.invoke(app, ["gen-whl", "--runtime", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    wheel.assert_called_once_with(
        fake_root,
        packages=["packages/runtime", "packages/connectors/pet_store"],
        host=False,
        all_=False,
        log=ANY,
    )


def test_wheel_bindings_flag(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.run_wheel_build") as wheel,
    ):
        result = runner.invoke(app, ["gen-whl", "--bindings"])
    assert result.exit_code == 0, result.output
    wheel.assert_called_once_with(
        fake_root, packages=["packages/bindings"], host=False, all_=False, log=ANY
    )


def test_wheel_host_and_all_conflict(fake_root: Path) -> None:
    with patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root):
        result = runner.invoke(app, ["gen-whl", "--connector-id", "pet_store", "--host", "--all"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_invalid_connector_id_is_a_usage_error(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build") as rb,
    ):
        result = runner.invoke(app, ["gen-all", "--connector-id", "Pet-Store", "--path", "s.yaml"])
    assert result.exit_code == 2
    assert "not a valid connector id" in result.output
    rb.assert_not_called()


def test_mcp_calls_run_mcp_build(fake_root: Path) -> None:
    for rel in (
        "packages/runtime/dist/runtime.whl",
        "packages/bindings/dist/bindings.whl",
        "packages/connectors/pet_store/dist/c.whl",
    ):
        path = fake_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    project = fake_root / "out" / "pet-store-nw-mcp"
    project.mkdir(parents=True)

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.run_mcp_build", return_value=project) as mcp,
    ):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store", "--force-output"])
    assert result.exit_code == 0, result.output
    mcp.assert_called_once_with(fake_root, "pet_store", force_output=True, tool_mode="list")
    assert "Wheel build" not in result.output  # wheels exist: no wheel stage listed


def test_docker_build_subprocess(fake_root: Path) -> None:
    project = fake_root / "nw-mcp-builder" / "out" / "pet-store-nw-mcp"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n")

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.build_image", return_value="pet-store-nw-mcp:latest") as docker,
    ):
        result = runner.invoke(app, ["docker-build", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    docker.assert_called_once_with(project, "pet-store-nw-mcp:latest", log=ANY)
    assert "pet-store-nw-mcp:latest" in result.output
    # The MCP host already exists (gen-all built it): its stages are not listed at all.
    assert "Wheel build" not in result.output
    assert "MCP host build" not in result.output


def test_docker_build_without_a_project_lists_the_stages_it_runs(fake_root: Path) -> None:
    for rel in ("packages/runtime/dist/r.whl", "packages/bindings/dist/b.whl"):
        (fake_root / rel).parent.mkdir(parents=True, exist_ok=True)
        (fake_root / rel).write_bytes(b"x")
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.prerequisites.is_interactive", return_value=True),
        patch("nw_cli.prerequisites.Confirm.ask", return_value=True),
        patch("nw_cli.cli.decide_tool_mode", return_value=MagicMock(mode="list", listing=None)),
        patch("nw_cli.cli.run_wheel_build") as wheel,
        patch("nw_cli.cli.run_mcp_build") as mcp,
        patch("nw_cli.cli.run_docker_build", return_value="pet-store-nw-mcp:latest"),
    ):
        result = runner.invoke(app, ["docker-build", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    assert wheel.call_args.kwargs["packages"] == ["packages/connectors/pet_store"]
    mcp.assert_called_once()
    assert "Wheel build" in result.output and "MCP host build" in result.output
    assert "(skipped)" not in result.output


def test_generate_stage_chaining_in_process(fake_root: Path) -> None:
    """Default generate chains connector → wheel → mcp → wire without re-invoking nw."""
    for rel in (
        "packages/runtime/dist/runtime.whl",
        "packages/bindings/dist/bindings.whl",
        "packages/connectors/pet_store/dist/c.whl",
    ):
        path = fake_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    project = fake_root / "nw-mcp-builder" / "out" / "pet-store-nw-mcp"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n")

    order: list[str] = []

    def fake_run_build(**kwargs):
        order.append("connector")
        assert kwargs["no_mcp"] is True
        assert kwargs["wire"] is True
        assert kwargs["connector_id"] == "pet_store"
        return 0

    def fake_wheel(*args, **kwargs):
        order.append("wheel")

    def fake_mcp(*args, **kwargs):
        order.append("mcp")
        assert kwargs.get("force_output") is False or args
        return project

    def fake_register(*args, **kwargs):
        order.append("wire")
        return True

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build", side_effect=fake_run_build) as rb,
        patch("nw_cli.cli.run_wheel_build", side_effect=fake_wheel) as wh,
        patch("nw_cli.cli.run_mcp_build", side_effect=fake_mcp) as mp,
        patch("nw_cli.cli.register_all_packages", side_effect=fake_register) as reg,
        patch("subprocess.run") as sub_run,
        patch("nw_cli.stages.subprocess.Popen") as popen,
    ):
        popen.return_value = MagicMock(stdout=iter([]), wait=MagicMock(return_value=0))
        result = runner.invoke(
            app,
            ["gen-all", "--connector-id", "pet_store", "--path", "spec.yaml"],
        )

    assert result.exit_code == 0, result.output
    assert order == ["connector", "wheel", "mcp", "wire"]
    rb.assert_called_once()
    assert rb.call_args.kwargs["no_mcp"] is True
    wh.assert_called_once()
    assert wh.call_args.kwargs["packages"] == [
        "packages/runtime",
        "packages/bindings",
        "packages/connectors/pet_store",
    ]
    mp.assert_called_once()
    reg.assert_called_once_with(fake_root, "pet_store")
    # No subprocess re-invocation of the nw CLI itself
    for c in sub_run.call_args_list:
        argv = c.args[0] if c.args else c.kwargs.get("args", [])
        if argv and str(argv[0]) == "nw":
            pytest.fail(f"unexpected nw subprocess: {argv}")


def test_generate_skip_flags(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build", return_value=0) as rb,
        patch("nw_cli.cli.run_wheel_build") as wh,
        patch("nw_cli.cli.run_mcp_build") as mp,
        patch("nw_cli.cli.register_all_packages") as reg,
    ):
        result = runner.invoke(
            app,
            [
                "gen-all",
                "--connector-id",
                "pet_store",
                "--path",
                "spec.yaml",
                "--no-wheel",
                "--no-mcp",
                "--no-wire",
            ],
        )
    assert result.exit_code == 0, result.output
    assert rb.call_args.kwargs["wire"] is False
    assert rb.call_args.kwargs["no_mcp"] is True
    wh.assert_not_called()
    mp.assert_not_called()
    reg.assert_not_called()


def test_prerequisite_non_interactive_exits(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.prerequisites.is_interactive", return_value=False),
        patch("nw_cli.cli.run_mcp_build") as mcp,
    ):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store"])
    assert result.exit_code == 1
    assert "nw gen-whl --runtime" in result.output or "nw gen-whl --runtime" in (
        result.stderr or ""
    )
    mcp.assert_not_called()


def test_prerequisite_interactive_builds_then_continues(fake_root: Path) -> None:
    project = fake_root / "out"
    project.mkdir()

    built: list[str] = []

    def build_runtime():
        built.append("runtime")
        path = fake_root / "packages" / "runtime" / "dist" / "r.whl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def build_bindings():
        built.append("bindings")
        path = fake_root / "packages" / "bindings" / "dist" / "b.whl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    def build_connector():
        built.append("connector")
        path = fake_root / "packages" / "connectors" / "pet_store" / "dist" / "c.whl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    builders = {
        "packages/runtime": build_runtime,
        "packages/bindings": build_bindings,
        "packages/connectors/pet_store": build_connector,
    }

    def fake_wheel_build(root, *, packages, **kw):
        for package in packages:
            builders[package]()

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.prerequisites.is_interactive", return_value=True),
        patch("nw_cli.prerequisites.Confirm.ask", return_value=True) as ask,
        patch("nw_cli.cli.run_wheel_build", side_effect=fake_wheel_build) as wheel,
        patch("nw_cli.cli.run_mcp_build", return_value=project) as mcp,
    ):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    ask.assert_called_once()  # one question for every missing wheel
    wheel.assert_called_once()
    assert "runtime" in built
    assert "bindings" in built
    assert "connector" in built
    mcp.assert_called_once()


def test_register_all_packages_idempotent(fake_root: Path) -> None:
    assert register_all_packages(fake_root, "pet_store") is True
    text = (fake_root / "scripts" / "build-packages.sh").read_text(encoding="utf-8")
    assert "packages/connectors/pet_store" in text

    assert register_all_packages(fake_root, "pet_store") is False
    text2 = (fake_root / "scripts" / "build-packages.sh").read_text(encoding="utf-8")
    assert text2.count("packages/connectors/pet_store") == 1


def test_run_wheel_build_subprocess_args(fake_root: Path) -> None:
    from nw_cli.stages import run_wheel_build

    with patch("nw_cli.stages.run_logged_command", return_value=0) as run:
        run_wheel_build(fake_root, connector_id="pet_store")
        cmd = run.call_args.args[0]
        # Relative POSIX path so Git Bash on Windows does not eat backslashes.
        assert cmd == [
            "bash",
            "scripts/build-packages.sh",
            "--linux-only",
            "packages/connectors/pet_store",
        ]
        assert "\\" not in cmd[1]
        assert run.call_args.kwargs["cwd"] == fake_root


def test_run_docker_build_subprocess_args(fake_root: Path) -> None:
    from nw_cli.stages import run_docker_build

    project = fake_root / "nw-mcp-builder" / "out" / "pet-store-nw-mcp"
    project.mkdir(parents=True)

    (project / "Dockerfile").write_text("FROM scratch\n")

    with patch("nw_cli.stages.run_logged_command", return_value=0) as run:
        image = run_docker_build(fake_root, "pet_store", tag="v1")
        assert image == "pet-store-nw-mcp:v1"
        cmd = run.call_args.args[0]
        assert cmd == ["docker", "build", "-t", "pet-store-nw-mcp:v1", "."]
        assert run.call_args.kwargs["cwd"] == project
        assert run.call_args.kwargs["env"]["DOCKER_BUILDKIT"] == "1"


def test_run_logged_command_streams_to_log(fake_root: Path) -> None:
    from nw_cli.stages import run_logged_command

    lines: list[str] = []
    proc = MagicMock()
    proc.stdout = iter(["hello\n", "world\n"])
    proc.wait.return_value = 0

    with patch("nw_cli.stages.subprocess.Popen", return_value=proc) as popen:
        code = run_logged_command(["echo", "hi"], cwd=fake_root, log=lines.append)
    assert code == 0
    assert lines == ["hello", "world"]
    assert popen.call_args.kwargs["stdout"] == subprocess.PIPE
    assert popen.call_args.kwargs["stderr"] == subprocess.STDOUT


def test_docker_build_a_stacklok_project_by_path(tmp_path: Path) -> None:
    project = tmp_path / "out" / "slack-mcp"
    project.mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n")
    with patch("nw_cli.stages.run_logged_command", return_value=0) as run:
        result = runner.invoke(app, ["docker-build", "--project", str(project), "--tag", "v1"])
    assert result.exit_code == 0, result.output
    assert run.call_args.args[0] == ["docker", "build", "-t", "slack-mcp:v1", "."]
    assert run.call_args.kwargs["cwd"] == project
    assert "slack-mcp:v1" in result.output


def test_docker_build_needs_exactly_one_target(tmp_path: Path) -> None:
    neither = runner.invoke(app, ["docker-build"])
    both = runner.invoke(app, ["docker-build", "--connector-id", "x", "--project", str(tmp_path)])
    no_dockerfile = runner.invoke(app, ["docker-build", "--project", str(tmp_path)])
    assert neither.exit_code == both.exit_code == no_dockerfile.exit_code == 2
    assert "Dockerfile" in no_dockerfile.output


def _generated(root: Path, rel: str, *, connectors: str | None = None, mtime: float) -> Path:
    import os

    project = root / rel
    (project / "config").mkdir(parents=True)
    (project / "Dockerfile").write_text("FROM scratch\n")
    if connectors:
        (project / "config" / "connectors.yaml").write_text(f"connectors:\n  {connectors}: {{}}\n")
    os.utime(project, (mtime, mtime))
    return project


def test_docker_build_finds_a_stacklok_server_by_connector_id(fake_root: Path) -> None:
    server = _generated(
        fake_root, "nw-stacklok-builder/out/slack-mcp", connectors="slack_web", mtime=2000
    )
    _generated(fake_root, "nw-stacklok-builder/out/other-mcp", connectors="stripe", mtime=3000)
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.build_image", return_value="slack-mcp:latest") as build,
    ):
        result = runner.invoke(app, ["docker-build", "--connector-id", "slack_web"])
    assert result.exit_code == 0, result.output
    build.assert_called_once_with(server, "slack-mcp:latest", log=ANY)


def test_several_projects_ask_on_a_terminal_and_take_the_newest_without(
    fake_root: Path,
) -> None:
    host = _generated(fake_root, "nw-mcp-builder/out/slack-web-nw-mcp", mtime=1000)
    newest = _generated(
        fake_root, "nw-stacklok-builder/out/slack-mcp", connectors="slack_web", mtime=3000
    )
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.build_image", return_value="x") as build,
    ):
        result = runner.invoke(app, ["docker-build", "--connector-id", "slack_web"])
        assert result.exit_code == 0, result.output
        assert build.call_args.args[0] == newest
        assert "building the newest" in result.output

        with (
            patch("nw_cli.cli.is_interactive", return_value=True),
            patch("nw_cli.cli.choose", return_value="2") as menu,
        ):
            result = runner.invoke(app, ["docker-build", "--connector-id", "slack_web"])
        assert result.exit_code == 0, result.output
        labels = [o.label for o in menu.call_args.args[2]]
        assert labels[0].startswith("stacklok server") and labels[1].startswith("MCP host")
        assert build.call_args.args == (host, "slack-web-nw-mcp:latest")
