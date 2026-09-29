# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Runtime error capture and reporting: one message, a documented exit code, no raw tracebacks."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nw_cli import prerequisites
from nw_cli.cli import app
from nw_cli.prerequisites import _probe_docker  # the real probe (conftest stubs the module's)
from nw_cli.progress import GenerateProgress, Stage
from nw_cli.stages import StageError, run_logged_command, run_wheel_build
from nw_cli.ui import ReportedError
from nw_cli.wheel_cache import is_fresh, record_build

runner = CliRunner()


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build-packages.sh").write_text(
        "#!/bin/bash\nALL_PACKAGES=(\n  packages/runtime\n)\n", encoding="utf-8"
    )
    for package in ("runtime", "bindings", "connectors/pet_store"):
        (tmp_path / "packages" / package).mkdir(parents=True)
    return tmp_path


def _gen_all(fake_root: Path, *extra: str, **patches):
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_connector_builder.pipeline.run_build", **patches.get("run_build", {})) as rb,
        patch("nw_cli.cli.run_wheel_build", **patches.get("wheel", {})) as wheel,
        patch("nw_cli.cli.run_mcp_build", return_value=fake_root / "out") as mcp,
        patch("nw_cli.cli.decide_tool_mode", return_value=MagicMock(mode="list", listing=None)),
        patch("nw_cli.cli.register_all_packages", return_value=True),
    ):
        result = runner.invoke(
            app, ["gen-all", "--connector-id", "pet_store", "--path", "spec.yaml", *extra]
        )
    return result, rb, wheel, mcp


def test_a_stage_failure_is_reported_once(fake_root: Path) -> None:
    result, _, _, mcp = _gen_all(
        fake_root, run_build={"side_effect": StageError("spec is not OpenAPI")}
    )
    assert result.exit_code == 1
    assert result.output.count("spec is not OpenAPI") == 1  # the panel only, no second error:
    assert "Failed at stage Connector codegen" in result.output
    assert "Log:" in result.output
    mcp.assert_not_called()


def test_an_unexpected_exception_is_a_message_not_a_traceback(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.mcp_project_dir", side_effect=KeyError("boom")),
    ):
        result = runner.invoke(app, ["docker-build", "--connector-id", "pet_store"])
    assert result.exit_code == 1
    assert "unexpected KeyError" in result.output
    assert "--debug" in result.output
    assert "Traceback" not in result.output


def test_debug_shows_the_traceback(fake_root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=fake_root),
        patch("nw_cli.cli.mcp_project_dir", side_effect=KeyError("boom")),
    ):
        result = runner.invoke(app, ["--debug", "docker-build", "--connector-id", "pet_store"])
    assert result.exit_code == 1
    assert "Traceback" in result.output


def test_ctrl_c_exits_130(fake_root: Path) -> None:
    result, *_ = _gen_all(fake_root, run_build={"side_effect": KeyboardInterrupt})
    assert result.exit_code == 130
    assert "Interrupted" in result.output


def test_a_root_error_exits_1_with_its_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with patch("nw_cli.root._package_dir", return_value=tmp_path / "nowhere" / "nw-cli"):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store"])
    assert result.exit_code == 1
    assert "node-wire repo root" in result.output


def test_a_wiring_failure_names_its_cause(fake_root: Path) -> None:
    report = fake_root / "packages" / "connectors" / "pet_store" / "report.json"
    report.write_text(json.dumps({"wire": {"ok": False, "error": "sample.env is read-only"}}))
    result, *_ = _gen_all(fake_root, run_build={"return_value": 1})
    assert result.exit_code == 1
    assert "sample.env is read-only" in result.output


def test_no_wheel_with_missing_wheels_fails_before_codegen(fake_root: Path) -> None:
    result, rb, _, _ = _gen_all(fake_root, "--no-wheel")
    assert result.exit_code == 1
    assert "--no-wheel" in result.output and "nw gen-whl --runtime --bindings" in result.output
    rb.assert_not_called()


def test_gen_all_reuses_wheels_whose_sources_are_unchanged(fake_root: Path) -> None:
    for package in ("runtime", "bindings", "connectors/pet_store"):
        dist = fake_root / "packages" / package / "dist"
        dist.mkdir()
        (dist / "x-1.0-py3-none-any.whl").write_bytes(b"x")
        record_build(fake_root, f"packages/{package}", "linux-only", since=0)
    (fake_root / "packages" / "connectors" / "pet_store" / "logic.py").write_text("changed = 1\n")

    result, _, wheel, _ = _gen_all(fake_root, run_build={"return_value": 0})
    assert result.exit_code == 0, result.output
    assert wheel.call_args.kwargs["packages"] == ["packages/connectors/pet_store"]
    assert "reused: packages/runtime, packages/bindings" in result.output

    result, _, wheel, _ = _gen_all(fake_root, "--rebuild-wheels", run_build={"return_value": 0})
    assert len(wheel.call_args.kwargs["packages"]) == 3


def test_a_stamp_is_stale_once_its_wheel_is_gone(fake_root: Path) -> None:
    dist = fake_root / "packages" / "runtime" / "dist"
    dist.mkdir()
    wheel = dist / "r-1.0-py3-none-any.whl"
    wheel.write_bytes(b"x")
    record_build(fake_root, "packages/runtime", "linux-only", since=0)
    assert is_fresh(fake_root, "packages/runtime", "linux-only")
    assert not is_fresh(fake_root, "packages/runtime", "host-only")
    wheel.unlink()
    assert not is_fresh(fake_root, "packages/runtime", "linux-only")


def test_docker_missing_fails_the_wheel_build_with_the_reason(
    fake_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prerequisites.docker_problem.cache_clear()
    monkeypatch.setattr(prerequisites, "_probe_docker", lambda: "the Docker daemon is not running")
    with patch("nw_cli.stages.run_logged_command", return_value=0) as run:
        with pytest.raises(prerequisites.PrerequisiteError, match="daemon is not running"):
            run_wheel_build(fake_root, runtime=True)
        run.assert_not_called()
        run_wheel_build(fake_root, runtime=True, host=True)  # host-only needs no Docker
        run.assert_called_once()


def test_probe_docker_reports_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prerequisites.shutil, "which", lambda _: None)
    assert "not on PATH" in (_probe_docker() or "")


def test_probe_docker_reports_a_stopped_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prerequisites.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(prerequisites.subprocess, "run", lambda *a, **k: MagicMock(returncode=1))
    assert "not running" in (_probe_docker() or "")


def test_a_missing_executable_is_a_stage_error(tmp_path: Path) -> None:
    with pytest.raises(StageError, match="`no-such-tool-xyz` not found on PATH"):
        run_logged_command(["no-such-tool-xyz"], cwd=tmp_path)


def test_an_interrupted_command_stops_its_child(tmp_path: Path) -> None:
    proc = MagicMock()
    proc.poll.return_value = None
    proc.stdout = iter(["line\n"])

    def interrupt(_line: str) -> None:
        raise KeyboardInterrupt

    with patch("nw_cli.stages.subprocess.Popen", return_value=proc):
        with pytest.raises(KeyboardInterrupt):
            run_logged_command(["bash", "x"], cwd=tmp_path, log=interrupt)
    proc.terminate.assert_called_once()


def test_quiet_output_is_logged_and_replayed_on_failure(tmp_path: Path) -> None:
    out = io.StringIO()
    log = tmp_path / "run.log"
    progress = GenerateProgress(
        stages=[Stage("wheel", "Wheels")],
        console=Console(file=out, force_terminal=False, width=100),
        log_path=log,
        stream=False,
    )
    with pytest.raises(ReportedError):
        with progress:

            def _build() -> None:
                progress.output("compiling runtime")
                print("printed by a library")
                raise StageError("Wheel build failed (exit 1)")

            progress.run_stage("wheel", _build)
    text = out.getvalue()
    assert "Last output:" in text
    assert "compiling runtime" in text and "printed by a library" in text
    logged = log.read_text()
    assert "compiling runtime" in logged and "printed by a library" in logged
    assert "error: Wheel build failed" in logged


def test_streamed_output_is_not_replayed(tmp_path: Path) -> None:
    out = io.StringIO()
    progress = GenerateProgress(
        stages=[Stage("wheel", "Wheels")],
        console=Console(file=out, force_terminal=False, width=100),
        stream=True,
    )
    with pytest.raises(ReportedError):
        with progress:
            progress.run_stage(
                "wheel",
                lambda: progress.output("step 1") or (_ for _ in ()).throw(StageError("x")),
            )
    text = out.getvalue()
    assert text.count("step 1") == 1
    assert "Last output:" not in text


def test_an_error_outside_any_stage_is_in_the_panel(tmp_path: Path) -> None:
    out = io.StringIO()
    progress = GenerateProgress(
        stages=[Stage("a", "Alpha")], console=Console(file=out, force_terminal=False, width=100)
    )
    with pytest.raises(ReportedError) as raised:
        with progress:
            raise StageError("Kept out/demo-mcp; pass --output-dir")
    assert raised.value.exit_code == 1
    assert "Kept out/demo-mcp" in out.getvalue()


def test_subprocess_failures_keep_their_exit_code_in_the_message(tmp_path: Path) -> None:
    proc = MagicMock(stdout=iter([]), wait=MagicMock(return_value=3))
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "build-packages.sh").write_text("#!/bin/bash\n")
    (tmp_path / "packages" / "runtime").mkdir(parents=True)
    with patch("nw_cli.stages.subprocess.Popen", return_value=proc):
        with pytest.raises(StageError, match="exit 3"):
            run_wheel_build(tmp_path, runtime=True, host=True)
    assert proc.wait.called
