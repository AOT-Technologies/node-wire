# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""``nw wire``, the gen-all stage order, and rerun hints from the state on disk (4.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from nw_cli.cli import app
from nw_cli.stages import StageError

runner = CliRunner()


def _flat(output: str) -> str:
    """The output with the panel's borders and line wrapping removed."""
    return " ".join(output.replace("│", " ").split())


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='node-wire'\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "connectors.yaml").write_text("connectors: {}\n", encoding="utf-8")
    (tmp_path / "nw-mcp-builder").mkdir()
    for package in ("runtime", "bindings", "connectors/pet_store"):
        (tmp_path / "packages" / package).mkdir(parents=True)
    return tmp_path


def _outcome(**overrides: Any) -> Any:
    from nw_connector_builder.pipeline import BuildOutcome

    fields = dict(
        stage_reached="promoted",
        promoted=True,
        mcp=None,
        wire=None,
        report={"summary": {}},
        report_path=Path("report.json"),
    )
    fields.update(overrides)
    return BuildOutcome(**fields)


def _gen_all(root: Path, *extra: str, order: list[str], **fail: Any):
    def step(name: str, result: Any = None):
        def run(*a: Any, **k: Any) -> Any:
            order.append(name)
            if name in fail:
                raise fail[name]
            return result

        return run

    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_connector_builder.pipeline.run_build", side_effect=step("connector", _outcome())),
        patch("nw_cli.cli.run_wire", side_effect=step("wire", "added")),
        patch("nw_cli.cli.run_wheel_build", side_effect=step("wheel", {})),
        patch("nw_cli.cli.run_mcp_build", side_effect=step("mcp", root / "out")),
        patch("nw_cli.cli.decide_tool_mode", return_value=MagicMock(mode="list", listing=None)),
    ):
        return runner.invoke(
            app, ["gen-all", "--connector-id", "pet_store", "--path", "spec.yaml", *extra]
        )


def test_gen_all_wires_before_the_mcp_host(root: Path) -> None:
    """The host bakes the connector's connectors.yaml entry, so wiring comes first."""
    order: list[str] = []
    result = _gen_all(root, order=order)
    assert result.exit_code == 0, result.output
    assert order == ["connector", "wire", "wheel", "mcp"]


def test_wire_command_wires_a_built_connector(root: Path) -> None:
    with (
        patch("nw_cli.cli.resolve_node_wire_root", return_value=root),
        patch("nw_cli.cli.run_wire", return_value="added") as wire,
    ):
        result = runner.invoke(app, ["wire", "--connector-id", "pet_store"])
    assert result.exit_code == 0, result.output
    wire.assert_called_once_with(root, "pet_store")
    assert "ALL_PACKAGES" in result.output and "added" in result.output


def test_a_failed_mcp_stage_suggests_force_output_only_when_a_project_exists(root: Path) -> None:
    order: list[str] = []
    result = _gen_all(root, order=order, mcp=StageError("boom"))
    assert result.exit_code == 1
    assert "uv run nw gen-mcp --connector-id pet_store" in _flat(result.output)
    assert "--force-output" not in result.output

    (root / "nw-mcp-builder" / "out" / "pet-store-nw-mcp").mkdir(parents=True)
    result = _gen_all(root, order=order, mcp=StageError("boom"))
    assert "--force-output" in _flat(result.output)


def test_a_failed_connector_stage_suggests_force_only_when_one_exists(root: Path) -> None:
    order: list[str] = []
    result = _gen_all(root, order=order, connector=StageError("bad spec"))
    assert "gen-all --connector-id pet_store --path spec.yaml" in _flat(result.output)
    assert "--force" not in result.output

    (root / "src" / "node_wire_pet_store").mkdir(parents=True)
    result = _gen_all(root, order=order, connector=StageError("bad spec"))
    assert "--path spec.yaml --force" in _flat(result.output)


def test_a_failed_wheel_stage_lists_only_the_wheels_still_to_build(root: Path) -> None:
    from nw_mcp_builder.wheel_cache import package_source_hash, stamp_build

    for package in ("packages/runtime", "packages/bindings"):
        (root / package / "dist").mkdir()
        (root / package / "dist" / "x-1.0-py3-none-any.whl").write_bytes(b"w")
        stamp_build(root, package, package_source_hash(root, package))
    order: list[str] = []
    result = _gen_all(root, "--rebuild-wheels", order=order, wheel=StageError("uv failed"))
    assert "uv run nw gen-whl --connector-id pet_store" in _flat(result.output)
    assert "--runtime" not in result.output


def test_the_mcp_project_name_comes_from_the_fixture(root: Path) -> None:
    from nw_cli.names import mcp_project_dir

    fixtures = root / "nw-mcp-builder" / "fixtures"
    fixtures.mkdir()
    (fixtures / "pet_store_nw.yaml").write_text("server:\n  name: petstore-custom\n")
    assert mcp_project_dir(root, "pet_store").name == "petstore-custom-mcp"
