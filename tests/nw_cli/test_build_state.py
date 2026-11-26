# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Wheel state and output-dir guarantees across ``nw`` commands (4.1–4.3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from nw_cli.cli import app
from nw_cli.stages import StageError, run_wheel_build
from nw_mcp_builder.wheel_cache import (
    WheelState,
    package_source_hash,
    stamp_build,
    wheel_state,
)

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[1] / "nw_stacklok_builder" / "fixtures" / "stacklok"
HOST = ["packages/runtime", "packages/bindings", "packages/connectors/pet_store"]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "node-wire"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "build-packages.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (root / "nw-mcp-builder").mkdir()
    for package in [*HOST, "packages/toolhive"]:
        (root / package).mkdir(parents=True)
        (root / package / "setup.py").write_text(package, encoding="utf-8")
    return root


def _builder(root: Path, *, fail: tuple[str, ...] = (), edit: str | None = None):
    def run(cmd: list[str], *, cwd: Path, log: Any = None, env: Any = None) -> int:
        for package in cmd[2:]:
            dist = root / package / "dist"
            dist.mkdir(exist_ok=True)
            for old in dist.glob("*.whl"):
                old.unlink()
            if package in fail:
                continue
            (dist / f"{Path(package).name}-1.0-py3-none-any.whl").write_bytes(b"w")
        if edit:
            (root / edit / "setup.py").write_text("edited during the build", encoding="utf-8")
        return 1 if fail else 0

    return run


def _stamp(root: Path, package: str) -> None:
    dist = root / package / "dist"
    dist.mkdir(exist_ok=True)
    (dist / f"{Path(package).name}-1.0-py3-none-any.whl").write_bytes(b"w")
    stamp_build(root, package, package_source_hash(root, package))


def test_each_package_is_stamped_as_it_succeeds(root: Path) -> None:
    with (
        patch(
            "nw_cli.stages.run_logged_command",
            side_effect=_builder(root, fail=("packages/bindings",)),
        ),
        pytest.raises(StageError, match="packages/bindings"),
    ):
        run_wheel_build(root, packages=HOST)
    assert wheel_state(root, "packages/runtime") is WheelState.FRESH
    assert wheel_state(root, "packages/connectors/pet_store") is WheelState.FRESH
    assert wheel_state(root, "packages/bindings") is WheelState.MISSING


def test_sources_edited_during_the_build_stay_stale(root: Path) -> None:
    with patch(
        "nw_cli.stages.run_logged_command", side_effect=_builder(root, edit="packages/runtime")
    ):
        run_wheel_build(root, packages=["packages/runtime"])
    assert wheel_state(root, "packages/runtime") is WheelState.STALE


def test_gen_mcp_rebuilds_a_wheel_whose_connector_was_regenerated(root: Path) -> None:
    """The old connector wheel is still in dist/: it must be rebuilt, never bundled."""
    for package in HOST:
        _stamp(root, package)
    (root / "packages/connectors/pet_store/setup.py").write_text("regenerated", encoding="utf-8")

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_cli.prerequisites.is_interactive", return_value=False),
        patch("nw_cli.cli.run_mcp_build") as mcp,
    ):
        result = runner.invoke(app, ["gen-mcp", "--connector-id", "pet_store"])
    assert result.exit_code == 1
    assert "nw gen-whl --connector-id pet_store" in result.output
    assert "--runtime" not in result.output
    mcp.assert_not_called()


def test_gen_all_no_wheel_refuses_stale_wheels(root: Path) -> None:
    for package in HOST:
        _stamp(root, package)
    (root / "packages/runtime/setup.py").write_text("changed", encoding="utf-8")
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_connector_builder.pipeline.run_build") as rb,
    ):
        result = runner.invoke(
            app, ["gen-all", "--connector-id", "pet_store", "--path", "s.yaml", "--no-wheel"]
        )
    assert result.exit_code == 1
    assert "packages/runtime (stale)" in result.output
    rb.assert_not_called()


# --- gen-stacklok output -------------------------------------------------------------------


def _scoped(tmp_path: Path):
    from nw_cli.stacklok import read_scope

    doc = yaml.safe_load((FIXTURES / "petstore.yaml").read_text())
    doc["spec"]["source"] = "openapi.json"
    (tmp_path / "openapi.json").write_text((FIXTURES / "petstore_openapi.json").read_text())
    path = tmp_path / "scope.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return read_scope(path, "pet_store")


def test_stacklok_generation_needs_fresh_wheels_before_writing(root: Path, tmp_path: Path) -> None:
    from nw_cli.stacklok import run_stacklok_generate

    out = tmp_path / "out"
    with (
        patch("mcp_builder.pipeline.run_pipeline") as pipeline,
        pytest.raises(StageError, match="packages/toolhive \\(missing\\)"),
    ):
        run_stacklok_generate(root, _scoped(tmp_path), output_dir=out, work_dir=tmp_path / "w")
    pipeline.assert_not_called()
    assert not out.exists() or list(out.iterdir()) == []


def test_a_failed_stacklok_regeneration_keeps_the_previous_project(
    root: Path, tmp_path: Path
) -> None:
    from nw_cli.stacklok import run_stacklok_generate

    for package in [*HOST, "packages/toolhive"]:
        _stamp(root, package)
    out = tmp_path / "out"
    previous = out / "petstore-mcp"
    previous.mkdir(parents=True)
    (previous / "kept.txt").write_text("previous", encoding="utf-8")

    def half_written(scope, spec, template, output_dir, **kwargs):
        (output_dir / "petstore-mcp" / "src").mkdir(parents=True)
        raise RuntimeError("`uv lock` failed")

    with (
        patch("mcp_builder.pipeline.run_pipeline", side_effect=half_written),
        pytest.raises(RuntimeError, match="uv lock"),
    ):
        run_stacklok_generate(
            root, _scoped(tmp_path), output_dir=out, work_dir=tmp_path / "w", force_output=True
        )
    assert (previous / "kept.txt").read_text(encoding="utf-8") == "previous"
    assert [p.name for p in out.iterdir()] == ["petstore-mcp"]


def test_stacklok_gets_the_resolved_wheels(root: Path, tmp_path: Path) -> None:
    from nw_cli.stacklok import run_stacklok_generate

    for package in [*HOST, "packages/toolhive"]:
        _stamp(root, package)
    seen = {}

    def generate(scope, spec, template, output_dir, **kwargs):
        seen.update(kwargs["node_wire"].wheels)
        (output_dir / "petstore-mcp").mkdir()
        return output_dir / "petstore-mcp"

    out = tmp_path / "out"
    with patch("mcp_builder.pipeline.run_pipeline", side_effect=generate):
        project = run_stacklok_generate(
            root, _scoped(tmp_path), output_dir=out, work_dir=tmp_path / "w"
        )
    assert project == out / "petstore-mcp" and project.is_dir()
    assert seen["node-wire-toolhive"].name == "toolhive-1.0-py3-none-any.whl"
    assert set(seen) == {
        "node-wire-runtime",
        "node-wire-bindings",
        "node-wire-toolhive",
        "node-wire-pet-store",
    }
