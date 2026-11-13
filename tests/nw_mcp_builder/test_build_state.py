# SPDX-FileCopyrightText: 2026 AOT Technologies
#
# SPDX-License-Identifier: Apache-2.0

"""Build-state guarantees of ``run_from_connector``: fresh wheels handed over, atomic output,
the output name from the loaded fixture, and a fixture that tracks the connector's actions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from nw_mcp_builder.from_connector import ensure_connector_fixture, run_from_connector
from nw_mcp_builder.wheel_cache import WheelsNotFreshError


def _run(root: Path, package_root: Path, **kwargs) -> Path:
    return run_from_connector(
        "demo_conn",
        node_wire_root=root,
        package_root=package_root,
        skip_build_wheels=True,
        **kwargs,
    )


def test_a_regenerated_connector_is_never_bundled_with_its_old_wheel(
    fake_node_wire: Path, package_root: Path
) -> None:
    """The connector's sources changed after its wheel was built: refuse, don't bundle it."""
    pkg = fake_node_wire / "packages" / "connectors" / "demo_conn"
    (pkg / "pyproject.toml").write_text('[project]\nname = "regenerated"\n', encoding="utf-8")

    with pytest.raises(WheelsNotFreshError, match="packages/connectors/demo_conn \\(stale\\)"):
        _run(fake_node_wire, package_root)
    assert list((package_root / "out").iterdir()) == []


def test_the_project_gets_the_stamped_wheels_not_the_newest_file(
    fake_node_wire: Path, package_root: Path
) -> None:
    dist = fake_node_wire / "packages" / "runtime" / "dist"
    (dist / "node_wire_runtime-9.9.9-py3-none-any.whl").write_bytes(b"newer, unstamped")

    project = _run(fake_node_wire, package_root)

    bundled = sorted(p.name for p in (project / "wheels").glob("node_wire_runtime-*.whl"))
    assert bundled == ["node_wire_runtime-1.0.0-py3-none-any.whl"]


def test_the_built_wheels_are_handed_to_the_project(
    fake_node_wire: Path, package_root: Path
) -> None:
    built = (
        fake_node_wire
        / "packages"
        / "runtime"
        / "dist"
        / "node_wire_runtime-1.0.0-py3-none-any.whl",
        fake_node_wire
        / "packages"
        / "bindings"
        / "dist"
        / "node_wire_bindings-1.0.0-py3-none-any.whl",
        fake_node_wire
        / "packages"
        / "connectors"
        / "demo_conn"
        / "dist"
        / "node_wire_demo_conn-1.0.0-py3-none-any.whl",
    )
    with (
        patch("nw_mcp_builder.from_connector.build_connector_wheels", return_value=built),
        patch("nw_mcp_builder.from_connector.fresh_wheels") as rescan,
    ):
        project = run_from_connector(
            "demo_conn", node_wire_root=fake_node_wire, package_root=package_root
        )
    rescan.assert_not_called()
    assert sorted(p.name for p in (project / "wheels").iterdir()) == sorted(w.name for w in built)


def test_a_failed_regeneration_keeps_the_previous_project(
    fake_node_wire: Path, package_root: Path
) -> None:
    project = _run(fake_node_wire, package_root)
    marker = project / "kept.txt"
    marker.write_text("previous", encoding="utf-8")

    with (
        patch("nw_mcp_builder.from_connector.write_env_example", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _run(fake_node_wire, package_root, force_output=True)

    assert marker.read_text(encoding="utf-8") == "previous"
    assert [p.name for p in (package_root / "out").iterdir()] == [project.name]


def test_a_replaced_project_holds_only_the_new_files(
    fake_node_wire: Path, package_root: Path
) -> None:
    project = _run(fake_node_wire, package_root)
    (project / "stale.txt").write_text("old", encoding="utf-8")

    again = _run(fake_node_wire, package_root, force_output=True)

    assert again == project
    assert not (project / "stale.txt").exists()
    assert (project / "Dockerfile").is_file()
    assert [p.name for p in (package_root / "out").iterdir()] == [project.name]


def test_leftover_staging_dirs_are_cleaned_on_start(
    fake_node_wire: Path, package_root: Path
) -> None:
    leftover = package_root / "out" / ".demo-conn-nw-mcp.tmp-999999999"
    (leftover / "demo-conn-nw-mcp").mkdir(parents=True)

    _run(fake_node_wire, package_root)

    assert not leftover.exists()


def test_preconditions_fail_before_anything_is_written(
    fake_node_wire: Path, package_root: Path
) -> None:
    (fake_node_wire / "config" / "connectors.yaml").write_text(
        "connectors:\n  other: {}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="demo_conn is not in"):
        _run(fake_node_wire, package_root)
    assert list((package_root / "out").iterdir()) == []


def test_the_output_name_comes_from_the_fixture(fake_node_wire: Path, package_root: Path) -> None:
    fixture = ensure_connector_fixture(
        "demo_conn", node_wire_root=fake_node_wire, fixtures_dir=package_root / "fixtures"
    )
    doc = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    doc["server"]["name"] = "renamed-host"
    fixture.write_text(yaml.safe_dump(doc), encoding="utf-8")
    (package_root / "out" / "renamed-host-mcp").mkdir()

    with pytest.raises(FileExistsError, match="renamed-host-mcp"):
        _run(fake_node_wire, package_root)

    project = _run(fake_node_wire, package_root, force_output=True)
    assert project.name == "renamed-host-mcp"


def test_a_fixture_is_regenerated_when_the_actions_change(fake_node_wire: Path) -> None:
    fixtures = fake_node_wire / "fixtures"
    path = ensure_connector_fixture(
        "demo_conn", node_wire_root=fake_node_wire, fixtures_dir=fixtures
    )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["server"]["description"] = "edited by hand"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    # Same actions: the hand edit is kept.
    ensure_connector_fixture("demo_conn", node_wire_root=fake_node_wire, fixtures_dir=fixtures)
    assert yaml.safe_load(path.read_text())["server"]["description"] == "edited by hand"

    logic = fake_node_wire / "src" / "node_wire_demo_conn" / "logic.py"
    logic.write_text(
        logic.read_text(encoding="utf-8").replace('@nw_action("ping")', '@nw_action("pinged")'),
        encoding="utf-8",
    )
    ensure_connector_fixture("demo_conn", node_wire_root=fake_node_wire, fixtures_dir=fixtures)
    tools = {t["tool_name"] for t in yaml.safe_load(path.read_text())["groups"][0]["tools"]}
    assert tools == {"pinged", "files_list"}


def test_an_existing_fixture_still_gets_the_name_checks(fake_node_wire: Path) -> None:
    fixtures = fake_node_wire / "fixtures"
    ensure_connector_fixture("demo_conn", node_wire_root=fake_node_wire, fixtures_dir=fixtures)
    logic = fake_node_wire / "src" / "node_wire_demo_conn" / "logic.py"
    logic.write_text(
        logic.read_text(encoding="utf-8").replace('@nw_action("ping")', '@nw_action("files_list")'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both map to the same tool_name"):
        ensure_connector_fixture("demo_conn", node_wire_root=fake_node_wire, fixtures_dir=fixtures)
